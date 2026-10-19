"""会话消息日志（REQ-20261019-222403 FR-005/DEC-005）。

服务端按会话持久化全部消息，作为对话历史的唯一事实源（SSE 只是投影）：
- 用户消息先落档再进执行（ledger 语义：意图先持久化，断连不丢）；
- 助手消息两阶段写：run 起始落空壳（status=running），终止时补内容与状态
  ——半截内容随 interrupted 落档并标注，绝不伪造成功（借鉴 ZCode：
  「完成」是记录上的字段，缺失即被打断的轮次）。

存储：workspace/.writing/conversations/<会话id>.jsonl，逐行追加事件记录，
读取时折叠（同 id 的最新状态生效）。坏行容错跳过（文件不因单行损坏全弃）。
纯同步小模块，不依赖 trace/recorder。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_CONVERSATIONS_RELPATH = ".writing/conversations"
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]")
# 用户消息生命周期：queued → running →（终态随本轮 run 收尾）
USER_STATUSES = frozenset({"queued", "running", "paused", "cancelled", "completed"})
# 助手记录生命周期：running → awaiting（HITL 提问腿）或终态
ASSISTANT_STATUSES = frozenset(
    {"running", "awaiting", "completed", "interrupted", "stopped", "failed"}
)


class ConversationLogError(RuntimeError):
    """用户消息落档失败——必须拒绝该轮（FR-005 失败语义），不得静默继续。"""


class ConversationMessage(BaseModel):
    """折叠后的会话消息（GET 会话端点与客户端恢复共用）。"""

    id: str
    role: str                       # user | assistant
    content: str = ""
    status: str                     # USER_STATUSES / ASSISTANT_STATUSES 成员
    trace_id: str | None = None
    ts: str = ""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _safe_filename(conversation_id: str) -> str:
    """会话 id → 文件名：替换不安全字符 + 剥离前导点划线 + 短哈希防碰撞。"""
    stem = _SAFE_ID.sub("_", conversation_id).strip("._")[:80] or "conv"
    digest = hashlib.sha1(conversation_id.encode("utf-8")).hexdigest()[:8]
    return f"{stem}.{digest}.jsonl"


class ConversationLog:
    """单 workspace 的会话日志读写入口（按会话 id 寻址文件）。"""

    def __init__(self, workspace_path: Path) -> None:
        self._root = Path(workspace_path) / _CONVERSATIONS_RELPATH

    def _path(self, conversation_id: str) -> Path:
        return self._root / _safe_filename(conversation_id)

    # ── 写侧 ────────────────────────────────────────────────────────────

    def _append(self, conversation_id: str, record: dict) -> None:
        path = self._path(conversation_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def admit_user(self, conversation_id: str, content: str, *, message_id: str | None = None) -> str | None:
        """用户消息先落档（ledger）。同一 message_id 重发（断连补跑）重新入队。

        落档失败抛 ConversationLogError——调用方必须拒绝该轮。
        返回 None = 拒绝重入队：该 id 已被停止清队标 cancelled（FR-006：
        停止=清空队列，被停掉的排队消息不得借重发复活）。
        """
        message_id = message_id or f"u-{uuid.uuid4().hex}"
        folded = self.read(conversation_id)
        user_ids = {m.id for m in folded if m.role == "user"}
        try:
            if message_id in user_ids:
                current = next(m for m in folded if m.id == message_id)
                if current.status == "cancelled":
                    return None
                self._append(conversation_id, {
                    "kind": "user_status", "id": message_id,
                    "status": "queued", "ts": _now_iso(),
                })
            else:
                self._append(conversation_id, {
                    "kind": "user", "id": message_id, "content": content,
                    "status": "queued", "ts": _now_iso(),
                })
        except OSError as exc:
            raise ConversationLogError(
                f"会话消息落档失败（用户意图未持久化，拒绝该轮）: {exc}"
            ) from exc
        return message_id

    def mark_user(self, conversation_id: str, message_id: str, status: str) -> None:
        """用户消息状态迁移（queued/running/paused/cancelled）。best-effort。"""
        if status not in USER_STATUSES:
            raise ValueError(f"unknown user status: {status}")
        try:
            self._append(conversation_id, {
                "kind": "user_status", "id": message_id, "status": status, "ts": _now_iso(),
            })
        except OSError:
            logger.warning("会话日志用户状态写失败 conv=%s msg=%s", conversation_id, message_id)

    def open_assistant(self, conversation_id: str, trace_id: str, *, assistant_id: str | None = None) -> str:
        """助手记录两阶段写·开壳（status=running）。best-effort 不阻断生成。"""
        assistant_id = assistant_id or f"a-{uuid.uuid4().hex}"
        try:
            self._append(conversation_id, {
                "kind": "assistant", "id": assistant_id, "trace_id": trace_id,
                "status": "running", "ts": _now_iso(),
            })
        except OSError:
            logger.warning("会话日志助手开壳写失败 conv=%s trace=%s", conversation_id, trace_id)
        return assistant_id

    def close_assistant(
        self, conversation_id: str, assistant_id: str, *, status: str, content: str = ""
    ) -> None:
        """助手记录收尾：补内容与终态（interrupted 携半截内容）。best-effort。"""
        if status not in ASSISTANT_STATUSES:
            raise ValueError(f"unknown assistant status: {status}")
        try:
            self._append(conversation_id, {
                "kind": "assistant_status", "id": assistant_id, "status": status,
                "content": content, "ts": _now_iso(),
            })
        except OSError:
            logger.warning("会话日志助手收尾写失败 conv=%s msg=%s", conversation_id, assistant_id)

    # ── 读侧 ────────────────────────────────────────────────────────────

    def read(self, conversation_id: str) -> list[ConversationMessage]:
        """折叠日志为有序消息列表（坏行跳过；同 id 最新状态生效）。"""
        path = self._path(conversation_id)
        if not path.is_file():
            return []
        order: list[str] = []
        folded: dict[str, dict] = {}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = record.get("kind")
            msg_id = record.get("id")
            if not isinstance(kind, str) or not isinstance(msg_id, str):
                continue
            if kind == "user":
                if msg_id not in folded:
                    order.append(msg_id)
                folded[msg_id] = {
                    "id": msg_id, "role": "user",
                    "content": str(record.get("content", "")),
                    "status": str(record.get("status", "queued")),
                    "ts": str(record.get("ts", "")),
                }
            elif kind == "assistant":
                if msg_id not in folded:
                    order.append(msg_id)
                folded[msg_id] = {
                    "id": msg_id, "role": "assistant", "content": "",
                    "status": str(record.get("status", "running")),
                    "trace_id": record.get("trace_id"),
                    "ts": str(record.get("ts", "")),
                }
            elif kind in ("user_status", "assistant_status"):
                entry = folded.get(msg_id)
                if entry is None:
                    continue
                entry["status"] = str(record.get("status", entry["status"]))
                if kind == "assistant_status" and "content" in record:
                    entry["content"] = str(record.get("content", ""))
        return [ConversationMessage(**folded[msg_id]) for msg_id in order if msg_id in folded]
