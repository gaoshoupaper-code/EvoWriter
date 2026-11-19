"""Private, durable instructions and explicitly published book artifacts."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import uuid
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from contracts.creative_harness import AGENTS, entry_missing, publication_path, readable_kind, write_denial
from contracts.writing_artifacts import artifact_relpath, parse_relpath

PROCESS = uuid.uuid4().hex
ACTIVE = ContextVar("creative_instruction", default=None)
CALL_ID = ContextVar("creative_call_id", default=None)
_DYNAMIC = "\n<!-- writer:人物动态 -->\n"


def private_root():
    configured = os.environ.get("CREATIVE_PRIVATE_ROOT")
    return Path(configured) if configured else Path(__file__).resolve().parents[3] / "checkpoints" / "creative"


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def valid_character_change(field, value, reason):
    return all(isinstance(s, str) and s.strip() and s.splitlines() == [s] and _DYNAMIC.strip() not in s
               for s in (field, value, reason))


def _read(path):
    if path.is_symlink():
        raise ValueError("不支持链接产物")
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(value, encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


async def remove_deleted_books(agent, store, recorder):
    """A previously offline suite also removes books erased while it was stopped."""
    root = private_root() / agent
    for marker in root.glob("*/owner.json"):
        try:
            if marker.parent.name.startswith(".deleted-") or marker.resolve() != marker.absolute():
                continue
            owner, workspace = json.loads(marker.read_text(encoding="utf-8"))
            if store.get_workspace(owner, workspace) is not None:
                continue
            directory = marker.parent
            with closing(sqlite3.connect(directory / "instructions.sqlite3")) as db:
                running = db.execute("SELECT id,trace FROM tasks WHERE status='running'").fetchall()
            stopping = False
            for task_id, trace in running:
                from app.domains.writing.creative_worker import cancel_private_instruction
                if cancel_private_instruction(task_id):
                    stopping = True
                if trace and recorder.cancel_run_task(trace):
                    stopping = True
            if stopping:
                continue
            directory.rename(root / (".deleted-" + directory.name))
        except (OSError, ValueError, sqlite3.Error):
            logging.getLogger(__name__).exception("删除作品私有记录失败，将再次清理")
    for directory in root.glob(".deleted-*"):
        if directory.resolve() == directory.absolute():
            try:
                await asyncio.to_thread(shutil.rmtree, directory)
            except OSError:
                logging.getLogger(__name__).exception("销毁已删除作品的私有文件失败，将再次清理")


class Book:
    def __init__(self, root, *, owner=None, workspace=None, evaluation=False):
        self.root = Path(root)
        meta = self.root / ".writing"
        meta.mkdir(parents=True, exist_ok=True)
        self.path = meta / "agents.sqlite3"
        scope = os.getenv("WRITING_AGENT_ID")
        if scope and scope not in AGENTS:
            raise ValueError("未知创作身份")
        identity = hashlib.sha256(os.fsencode(self.root.resolve())).hexdigest()[:32]
        self.private = private_root() / scope / identity if scope and not evaluation else meta / "private"
        self.private.mkdir(parents=True, exist_ok=True)
        with self.tx() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS private.sessions(thread TEXT PRIMARY KEY, agent TEXT NOT NULL, last_seen INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS private.tasks(id TEXT PRIMARY KEY, agent TEXT, thread TEXT, msg TEXT, payload TEXT,
                    volume INTEGER, chapter INTEGER, rev TEXT, status TEXT, pid TEXT, trace TEXT,
                    UNIQUE(thread,msg));
                CREATE TABLE IF NOT EXISTS private.files(task TEXT, path TEXT, text TEXT, PRIMARY KEY(task,path));
                CREATE TABLE IF NOT EXISTS private.saves(task TEXT, call TEXT, path TEXT, receipt TEXT, PRIMARY KEY(task,call));
                CREATE TABLE IF NOT EXISTS pending(path TEXT PRIMARY KEY, text TEXT);
                CREATE TABLE IF NOT EXISTS private.interrupts(task TEXT PRIMARY KEY, payload TEXT);
                CREATE TABLE IF NOT EXISTS private.attempts(task TEXT, source TEXT, request TEXT, payload TEXT,
                    PRIMARY KEY(task,request));
                CREATE TABLE IF NOT EXISTS cards(name TEXT PRIMARY KEY, owner TEXT, volume INTEGER, role TEXT);
                CREATE TABLE IF NOT EXISTS changes(name TEXT, field TEXT, value TEXT, PRIMARY KEY(name,field));
                CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, name TEXT, field TEXT, value TEXT,
                    chapter INTEGER, reason TEXT);
                CREATE TABLE IF NOT EXISTS conflicts(id INTEGER PRIMARY KEY, name TEXT, field TEXT,
                    prior TEXT, proposal TEXT, reason TEXT, chapter INTEGER, resolved INTEGER DEFAULT 0);
            """)
        marker = self.private / "owner.json"
        if owner and workspace and not marker.is_file():
            _put(marker, _json([owner, workspace]))

    @contextmanager
    def tx(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute("ATTACH DATABASE ? AS private", (str(self.private / "instructions.sqlite3"),))
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        finally:
            db.close()

    def export(self):
        # Commit the journal before publishing files. A killed exporter can repeat
        # an exact replacement without re-appending a chapter or character change.
        with self.tx() as db:
            for p, text in db.execute("SELECT path,text FROM pending"):
                self.file(p)
                _put(self.root / p, text)
            db.execute("DELETE FROM pending")

    def file(self, rel):
        path = self.root / rel
        if path.resolve() != self.root.resolve() / rel:
            raise ValueError("产物路径不可包含链接")
        return path

    def register_thread(self, agent, thread):
        if agent not in AGENTS:
            raise ValueError("未知创作入口")
        with self.tx() as db:
            db.execute("INSERT INTO private.sessions VALUES (?,?,(SELECT COALESCE(MAX(last_seen),0)+1 FROM private.sessions))", (thread, agent))

    def threads(self, agent):
        with self.tx() as db:
            return [r[0] for r in db.execute("SELECT thread FROM private.sessions WHERE agent=? ORDER BY last_seen DESC", (agent,))]

    def touch(self, agent, thread):
        with self.tx() as db:
            count = db.execute("UPDATE private.sessions SET last_seen=(SELECT MAX(last_seen)+1 FROM private.sessions) WHERE agent=? AND thread=?",
                               (agent, thread)).rowcount
            if not count:
                raise ValueError("会话不属于本入口")

    def session_dir(self, agent, thread):
        if thread not in self.threads(agent):
            raise ValueError("会话不属于本入口")
        path = self.private / "sessions" / thread
        path.mkdir(parents=True, exist_ok=True)
        return path

    def snapshot(self, agent, db):
        roots = [self.root] + [self.root / d for d in ("卷纲", "章纲", "正文", "人物")]
        paths = {r[0] for r in db.execute("SELECT path FROM pending")}
        for base in roots:
            if base.is_symlink():
                raise ValueError("产物目录不可包含链接")
            for path in sorted(base.glob("*.md")):
                paths.add(path.relative_to(self.root).as_posix())
        for rel in sorted(paths):
            ref = parse_relpath(rel)
            if ref and readable_kind(agent, ref.kind):
                yield rel, self.current(db, rel)

    def start(self, agent, thread, msg, payload, volume, chapter, revision):
        self.touch(agent, thread)
        self.export()
        with self.tx() as db:
            old = db.execute("SELECT * FROM private.tasks WHERE thread=? AND msg=?", (thread, msg)).fetchone()
            if old:
                if json.loads(old["payload"]) != payload or old["volume"] != volume or old["chapter"] != chapter:
                    raise ValueError("消息编号已用于不同指令")
                return Task(self, dict(old))
            if db.execute("SELECT 1 FROM private.tasks WHERE thread=? AND status IN ('running','awaiting_input','interrupted')", (thread,)).fetchone():
                raise ValueError("请先继续或停止该会话的现有任务")
            if missing := entry_missing(self.root, agent, volume, chapter):
                raise ValueError("缺少资料：" + "、".join(missing))
            task = uuid.uuid4().hex
            db.execute("INSERT INTO private.tasks VALUES (?,?,?,?,?,?,?,?,?,?,NULL)",
                       (task, agent, thread, msg, _json(payload), volume, chapter, revision, "new", PROCESS))
            db.executemany("INSERT INTO private.files VALUES (?,?,?)", ((task, p, s) for p, s in self.snapshot(agent, db)))
            row = dict(db.execute("SELECT * FROM private.tasks WHERE id=?", (task,)).fetchone())
        return Task(self, row)

    def task(self, agent, *, task_id=None, thread=None):
        if task_id is None and thread is None:
            return None
        with self.tx() as db:
            row = db.execute("SELECT * FROM private.tasks WHERE agent=? AND (id=? OR thread=?) ORDER BY rowid DESC LIMIT 1",
                             (agent, task_id, thread)).fetchone()
            if row is None:
                return None
            if row["status"] == "running" and row["pid"] != PROCESS:
                db.execute("UPDATE private.tasks SET status='interrupted' WHERE id=?", (row["id"],))
                row = dict(row) | {"status": "interrupted"}
            return Task(self, dict(row))

    def conflicts(self, agent):
        if agent != "text":
            return []
        with self.tx() as db:
            return [dict(r) for r in db.execute(
                "SELECT id,name,field,prior AS current,proposal AS proposed FROM conflicts WHERE resolved=0 ORDER BY id")]

    def resolve(self, agent, cid, choice):
        if agent != "text" or choice not in ("current", "proposed"):
            raise ValueError("无法处理人物冲突")
        self.export()
        with self.tx() as db:
            row = db.execute("SELECT * FROM conflicts WHERE id=?", (cid,)).fetchone()
            if not row:
                raise ValueError("人物冲突不存在")
            if row["resolved"]:
                return
            live = db.execute("SELECT value FROM changes WHERE name=? AND field=?", (row["name"], row["field"])).fetchone()
            current = live[0] if live else None
            if current not in (row["prior"], row["proposal"]):
                # A third instruction landed after these choices were displayed.
                # Show its value before letting either choice replace that change.
                db.execute("UPDATE conflicts SET prior=? WHERE id=?", (current, cid))
                return
            if choice == "proposed" and current != row["proposal"]:
                self._change(db, row["name"], row["field"], row["proposal"], row["chapter"], row["reason"])
            db.execute("UPDATE conflicts SET resolved=1 WHERE id=?", (cid,))
        self.export()

    def _change(self, db, name, field, value, chapter, reason):
        db.execute("INSERT OR REPLACE INTO changes VALUES (?,?,?)", (name, field, value))
        db.execute("INSERT INTO history(name,field,value,chapter,reason) VALUES (?,?,?,?,?)", (name, field, value, chapter, reason))
        rel = artifact_relpath("character", name=name)
        current = self.current(db, rel)
        if current is None:
            raise ValueError("人物卡不存在")
        result = current.split(_DYNAMIC)[0].rstrip() + _DYNAMIC + "## 人物现状\n"
        for key, item in db.execute("SELECT field,value FROM changes WHERE name=? ORDER BY field", (name,)):
            result += f"- {key}：{item}\n"
        result += "\n## 变化记录\n"
        for ch, key, item, why in db.execute("SELECT chapter,field,value,reason FROM history WHERE name=? ORDER BY id", (name,)):
            result += f"- 第{ch}章 · {key}：{item}（{why}）\n"
        db.execute("INSERT OR REPLACE INTO pending VALUES (?,?)", (rel, result))
        return result

    def current(self, db, rel):
        pending = db.execute("SELECT text FROM pending WHERE path=?", (rel,)).fetchone()
        return pending[0] if pending else _read(self.file(rel))


@dataclass
class Task:
    book: Book
    row: dict
    recovering: bool = False

    @property
    def id(self):
        return self.row["id"]

    @property
    def agent(self):
        return self.row["agent"]

    @property
    def view(self):
        return self.book.private / "instructions" / self.id

    def info(self):
        with self.book.tx() as db:
            progress = [r[0] for r in db.execute("SELECT DISTINCT path FROM private.saves WHERE task=?", (self.id,))]
            iv = db.execute("SELECT payload FROM private.interrupts WHERE task=?", (self.id,)).fetchone()
        return {"task_id": self.id, "thread_id": self.row["thread"], "status": self.row["status"],
                "progress": progress, "volume": self.row["volume"], "chapter": self.row["chapter"],
                "harness_commit": self.row["rev"], "trace_id": self.row["trace"],
                "awaiting_input": json.loads(iv[0]) if iv and self.row["status"] == "awaiting_input" else None}

    def interrupt(self, payload):
        with self.book.tx() as db:
            changed = db.execute("UPDATE private.tasks SET status='awaiting_input' WHERE id=? AND status='running'", (self.id,))
            if changed.rowcount:
                db.execute("INSERT OR REPLACE INTO private.interrupts VALUES (?,?)", (self.id, _json(payload)))
            self.row["status"] = db.execute("SELECT status FROM private.tasks WHERE id=?", (self.id,)).fetchone()[0]

    def begin(self, *, resume=False):
        self.book.export()
        with self.book.tx() as db:
            expected = ("awaiting_input", "interrupted") if resume else ("new",)
            row = db.execute("SELECT status,pid FROM private.tasks WHERE id=?", (self.id,)).fetchone()
            status = "interrupted" if row["status"] == "running" and row["pid"] != PROCESS else row["status"]
            if status not in expected:
                raise ValueError(f"任务当前状态为 {status}")
            self.recovering = status == "interrupted"
            for rel, value in db.execute("SELECT path,text FROM private.files WHERE task=?", (self.id,)):
                _put(self.view / rel, value)
            # Only the sandbox's phase is written. Shared book state is never used for routing.
            from contracts.writing_artifacts import load_writing_state, save_writing_state
            state = load_writing_state(self.view)
            state.stage = state.confirmed_stage = self.agent
            save_writing_state(self.view, state)
            db.execute("UPDATE private.tasks SET status='running',pid=? WHERE id=?", (PROCESS, self.id))
            self.row["status"] = "running"

    def mark(self, status, trace=None):
        with self.book.tx() as db:
            db.execute("UPDATE private.tasks SET status=?, trace=COALESCE(?,trace) WHERE id=? AND status!='stopped'", (status, trace, self.id))
            self.row.update(dict(db.execute("SELECT status,trace FROM private.tasks WHERE id=?", (self.id,)).fetchone()))

    def prior_attempts(self):
        with self.book.tx() as db:
            return [dict(r) for r in db.execute(
                "SELECT source,request,payload FROM private.attempts WHERE task=? ORDER BY rowid", (self.id,))]

    def note_attempt(self, source, request, payload):
        with self.book.tx() as db:
            db.execute("INSERT OR IGNORE INTO private.attempts VALUES (?,?,?,?)",
                       (self.id, source, request, _json(payload)))

    def require_write(self, tool, args):
        return write_denial(self.agent, tool, args, self.row["volume"], self.row["chapter"])

    def apply_worker_save(self, call_id, args, content):
        if not isinstance(call_id, str) or not call_id or not isinstance(args, dict) or not isinstance(content, str):
            raise ValueError("产物提交格式无效")
        if reason := self.require_write("save_artifact", args):
            return "[拒绝] " + reason
        rel = publication_path(args)
        def publish():
            _put(self.view / rel, content)
            return "已落盘 " + rel
        token = CALL_ID.set(call_id)
        try:
            return self.save(args, publish)
        finally:
            CALL_ID.reset(token)

    def apply_worker_character(self, call_id, name, field, value, reason):
        if not isinstance(call_id, str) or not call_id or not all(isinstance(v, str) for v in (name, field, value, reason)):
            raise ValueError("人物变更格式无效")
        token = CALL_ID.set(call_id)
        try:
            return self.character(name, field, value, reason)
        finally:
            CALL_ID.reset(token)

    def save(self, args, save):
        reason = self.require_write("save_artifact", args)
        if reason:
            return "[拒绝] " + reason
        rel = publication_path(args)
        def write(db, previous):
            current = self.book.current(db, rel)
            if args["kind"] == "character":
                role = args.get("character_role")
                owner = db.execute("SELECT owner,volume,role FROM cards WHERE name=?", (args["name"],)).fetchone()
                if owner and (owner[0] != self.agent or owner[2] != role or (self.agent == "volume" and owner[1] != self.row["volume"])):
                    return "[拒绝] 此人物原始设定属于其他创作范围", None
            strip = lambda s: s.split(_DYNAMIC)[0].rstrip() if s is not None else None
            if (strip(current) != strip(previous) if args["kind"] == "character" else current != previous):
                return "[拒绝] 稿件已更新，请在下一条指令中合并修改", None
            result = save()
            text = _read(self.view / rel)
            if text is None or text == previous:
                return result, None
            if args["kind"] == "character":
                db.execute("INSERT OR IGNORE INTO cards VALUES (?,?,?,?)", (args["name"], self.agent, self.row["volume"], role))
                text = text.split(_DYNAMIC)[0] + (_DYNAMIC + current.split(_DYNAMIC, 1)[1] if current and _DYNAMIC in current else "")
            db.execute("INSERT OR REPLACE INTO pending VALUES (?,?)", (rel, text))
            return result, text
        return self._operation(rel, write)

    def character(self, name, field, value, reason):
        if self.agent != "text":
            return "[拒绝] 仅正文可以回写人物现状"
        if not valid_character_change(field, value, reason):
            return "[拒绝] 人物字段、现状和变化原因应各为一行"
        rel = artifact_relpath("character", name=name)
        def write(db, previous):
            if not previous:
                return "[拒绝] 人物卡尚不存在", None
            # Only a field actually read or written in this instruction can be
            # considered its base. A neighbouring field merge must not silently
            # refresh the task's view of a conflicting change from another task.
            frozen = previous.partition(_DYNAMIC)[2].partition("## 变化记录")[0].splitlines()
            old = next((line[len(field)+3:] for line in frozen if line.startswith(f"- {field}：")), None)
            row = db.execute("SELECT value FROM changes WHERE name=? AND field=?", (name, field)).fetchone()
            live = row[0] if row else None
            if live != old and live != value:
                db.execute("INSERT INTO conflicts(name,field,prior,proposal,reason,chapter) VALUES (?,?,?,?,?,?)",
                           (name, field, live, value, reason, self.row["chapter"]))
                return "已保存两项人物现状，等待作者选择", previous
            self.book._change(db, name, field, value, self.row["chapter"], reason)
            local_state = previous.partition(_DYNAMIC)[2].partition("## 变化记录")
            lines = [line for line in local_state[0].splitlines()
                     if line.startswith("- ") and not line.startswith(f"- {field}：")]
            lines.append(f"- {field}：{value}")
            record = f"- 第{self.row['chapter']}章 · {field}：{value}（{reason}）"
            local = (previous.split(_DYNAMIC)[0].rstrip() + _DYNAMIC + "## 人物现状\n" +
                     "\n".join(lines) + "\n\n## 变化记录\n" + (local_state[2].strip() + "\n" if local_state[2].strip() else "") + record + "\n")
            return "已更新人物卡现状和变化记录", local
        return self._operation(rel, write)

    def _operation(self, rel, callback):
        call = CALL_ID.get()
        if not call:
            raise ValueError("缺少工具调用编号，无法安全保存")
        with self.book.tx() as db:
            done = db.execute("SELECT receipt FROM private.saves WHERE task=? AND call=?", (self.id, call)).fetchone()
            if done:
                return json.loads(done[0])
            if db.execute("SELECT status FROM private.tasks WHERE id=?", (self.id,)).fetchone()[0] != "running":
                raise ValueError("任务已经停止")
            prior = db.execute("SELECT text FROM private.files WHERE task=? AND path=?", (self.id, rel)).fetchone()
            old = prior[0] if prior else None
            view = self.view / rel
            if old is None:
                view.unlink(missing_ok=True)
            else:
                _put(view, old)
            result, text = callback(db, old)
            if text is not None:
                db.execute("INSERT OR REPLACE INTO private.files VALUES (?,?,?)", (self.id, rel, text))
                db.execute("INSERT INTO private.saves VALUES (?,?,?,?)", (self.id, call, rel, _json(result)))
                _put(view, text)
        self.book.export()
        return result
