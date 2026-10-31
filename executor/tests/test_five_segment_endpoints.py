"""五段产物树与正文端点测试（REQ-20261018-115508 FR-009/011，AC-011/013 数据面）。

轻量 TestClient（只挂 workspaces 路由 + 依赖覆盖），锁定：
  - _read_outline_content：five 工作区返回五段产物树（顺序：主题→总纲→文风→卷纲→章纲→正文）。
  - GET/PUT manuscript：人修保存直写、finalize 改状态行、缺失 404/exists=False。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import current_user
from app.platform.trace import TraceDetail, TraceRunSummary
from app.platform.trace.recorder import TraceLogEvent
from app.routers import workspaces as ws_router


class _FakeStore:
    def __init__(self, workspace_path: Path) -> None:
        self._ws = SimpleNamespace(workspace_path=str(workspace_path))
        self._thread = SimpleNamespace(
            thread_id="t1", workspace_id="ws1", workspace_path=str(workspace_path),
        )

    def get_workspace(self, owner_id: str, workspace_id: str):
        return self._ws if workspace_id == "ws1" else None

    def get_thread(self, owner_id: str, thread_id: str):
        return self._thread if owner_id == "u1" and thread_id == "t1" else None


class _FakeRecorder:
    def __init__(self, workspace_path: Path) -> None:
        self.workspace_path = workspace_path
        self.run: TraceRunSummary | None = None
        self.events: list[TraceLogEvent] = []
        self.lookups: list[tuple[str, str | None]] = []
        self.cancelled: list[tuple[str, str]] = []

    def cancel_awaiting_run(self, thread, trace_id: str, reason: str):
        assert thread.thread_id == "t1"
        assert self.run is not None and self.run.trace_id == trace_id
        assert self.run.status == "awaiting_input"
        self.cancelled.append((trace_id, reason))
        self.run.status = "cancelled"

    def find_run_by_trace_id(self, trace_id: str, workspace_path: str | None = None):
        self.lookups.append((trace_id, workspace_path))
        return self.run if self.run is not None and self.run.trace_id == trace_id else None

    def read_run(self, thread, trace_id: str):
        assert thread.thread_id == "t1"
        assert self.run is not None and self.run.trace_id == trace_id
        return TraceDetail(run=self.run, events=self.events, nodes=[], context=[], todos=[])

    def await_review(self, review_id: str, review_kind: str = "stage", *, choice: bool = False) -> None:
        self.run = TraceRunSummary(
            trace_id="trace-loop", workspace_id="ws1", thread_id="t1", session_name="创作",
            workspace_path=str(self.workspace_path), endpoint="screenplay.generate.stream",
            status="awaiting_input", started_at="2026-10-29T01:00:00+00:00",
            path="traces/20261029-0100/trace-loop.jsonl",
        )
        args = {"question": "本批已完成，请审查", "options": []}
        if not choice:
            args["review_kind"] = review_kind
        for kind, extra in (
            ("tool_start", {"status": "running", "tool_name": "confirm_with_user" if choice else "request_review",
                            "tool_args": args}),
            ("hitl", {"status": "awaiting_input"}),
        ):
            seq = len(self.events) + 1
            self.events.append(TraceLogEvent.model_validate({
                "trace_id": self.run.trace_id, "event_id": f"trace-loop-{seq}", "sequence": seq,
                "type": kind, "timestamp": "2026-10-29T01:00:00+00:00", "source": "runtime",
                "agent_name": "author", "tool_call_id": review_id, **extra,
            }))


@pytest.fixture()
def recorder(tmp_path: Path) -> _FakeRecorder:
    return _FakeRecorder(tmp_path)


@pytest.fixture()
def client(tmp_path: Path, monkeypatch, recorder: _FakeRecorder) -> TestClient:
    app = FastAPI()
    app.include_router(ws_router.router)
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(user_id="u1")
    monkeypatch.setattr(ws_router, "get_thread_store", lambda: _FakeStore(tmp_path))
    monkeypatch.setattr(ws_router, "get_trace_recorder", lambda: recorder)
    return TestClient(app)


def _seed_book(ws: Path) -> None:
    (ws / "主题.md").write_text("# 主题 · 书\n> 状态：定稿\n", encoding="utf-8")
    (ws / "总纲.md").write_text("# 总纲 · 书\n> 状态：定稿\n", encoding="utf-8")
    (ws / "文风.md").write_text("# 文风 · 书\n> 状态：风格卡已定\n", encoding="utf-8")
    (ws / "卷纲").mkdir()
    (ws / "卷纲" / "第01卷.md").write_text("# 卷纲 · 第 1 卷\n", encoding="utf-8")
    (ws / "章纲").mkdir()
    (ws / "章纲" / "第001章.md").write_text("# 章纲 · 第001章\n", encoding="utf-8")
    (ws / "正文").mkdir()
    (ws / "正文" / "第001章开局.md").write_text(
        "# 第001章 · 开局\n> 状态：初稿｜2026-10-18\n\n正文内容", encoding="utf-8"
    )


def test_outline_tree_returns_five_segments(client: TestClient, tmp_path: Path) -> None:
    _seed_book(tmp_path)
    resp = client.get("/workspaces/ws1/outline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "five"
    rels = [f["rel"] for f in body["files"]]
    assert rels == [
        "主题.md", "总纲.md", "文风.md",
        "卷纲/第01卷.md", "章纲/第001章.md", "正文/第001章开局.md",
    ]
    assert body["pipeline"]["kind"] == "five"


def test_outline_tree_empty_book(client: TestClient, tmp_path: Path) -> None:
    resp = client.get("/workspaces/ws1/outline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "five" and body["files"] == []


def test_manuscript_get_missing(client: TestClient) -> None:
    resp = client.get("/workspaces/ws1/manuscript/9")
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is False and body["status"] == "draft"


def test_manuscript_roundtrip_and_finalize(client: TestClient, tmp_path: Path) -> None:
    _seed_book(tmp_path)
    got = client.get("/workspaces/ws1/manuscript/1").json()
    assert got["exists"] is True and got["status"] == "draft"
    assert "正文内容" in got["markdown"]

    edited = got["markdown"].replace("正文内容", "人修后的正文内容")
    resp = client.put("/workspaces/ws1/manuscript/1", json={"markdown": edited, "finalize": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "finalized"
    disk = (tmp_path / "正文" / "第001章开局.md").read_text(encoding="utf-8")
    assert "人修定稿" in disk and "人修后的正文内容" in disk


def test_manuscript_save_without_finalize_keeps_status(client: TestClient, tmp_path: Path) -> None:
    _seed_book(tmp_path)
    resp = client.put(
        "/workspaces/ws1/manuscript/1",
        json={"markdown": "# 第001章 · 开局\n> 状态：初稿｜2026-10-18\n\n改了一版", "finalize": False},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "draft"
    assert "初稿" in (tmp_path / "正文" / "第001章开局.md").read_text(encoding="utf-8")


def test_manuscript_put_missing_404(client: TestClient) -> None:
    resp = client.put("/workspaces/ws1/manuscript/9", json={"markdown": "x", "finalize": False})
    assert resp.status_code == 404


def test_manuscript_finalize_without_status_line(client: TestClient, tmp_path: Path) -> None:
    """T5：正文无状态行时 finalize 走 insert(1)——标题仍居首行。"""
    (tmp_path / "正文").mkdir()
    (tmp_path / "正文" / "第002章.md").write_text(
        "# 第002章 · 无状态行\n\n正文", encoding="utf-8"
    )
    resp = client.put(
        "/workspaces/ws1/manuscript/2",
        json={"markdown": "# 第002章 · 无状态行\n\n正文改", "finalize": True},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "finalized"
    disk = (tmp_path / "正文" / "第002章.md").read_text(encoding="utf-8")
    lines = disk.splitlines()
    assert lines[0] == "# 第002章 · 无状态行"
    assert "人修定稿" in lines[1]


def test_watch_classify_five_tree(tmp_path: Path) -> None:
    """T6：五段产物树变更全归 outline 事件；.writing 状态文件不归类。"""
    from app.routers.workspaces import _classify_changes

    def _changes(*rels):
        return [("modified", str(tmp_path / r)) for r in rels]

    cats = _classify_changes(_changes("主题.md", "总纲.md", "文风.md", "卷纲/第01卷.md", "章纲/第001章.md", "正文/第001章.md"), tmp_path)
    assert cats == {"outline"}
    assert _classify_changes(_changes(".writing/state.json"), tmp_path) == set()
    assert _classify_changes(_changes("outline/总纲.md"), tmp_path) == {"outline"}


# ── 人工段指针端点（REQ-20261019-232617 FR-002/004，AC-003/006）─────


def test_stage_pointer_update_roundtrip(client: TestClient, tmp_path: Path) -> None:
    """AC-003：点确认写指针，返回新 phase 与就绪信号。"""
    from contracts.writing_artifacts import load_writing_state

    (tmp_path / "主题.md").write_text(
        "# 主题 · 书\n> 状态：定稿\n\n## 一句话主题\nx\n\n## 约束级\n"
        "### 核心爽点\nx\n### 核心矛盾\nx\n### 核心爽点剧情\nx\n", encoding="utf-8",
    )
    resp = client.put("/workspaces/ws1/writing/stage", json={"stage": "outline"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["phase"] == "outline"
    assert load_writing_state(tmp_path).confirmed_stage == "outline"
    # 回退同样走同一入口（AC-006）
    resp = client.put("/workspaces/ws1/writing/stage", json={"stage": "theme"})
    assert resp.status_code == 200
    assert resp.json()["phase"] == "theme"
    assert load_writing_state(tmp_path).confirmed_stage == "theme"


def test_stage_pointer_rejects_invalid_and_legacy(client: TestClient, tmp_path: Path) -> None:
    resp = client.put("/workspaces/ws1/writing/stage", json={"stage": "不存在的段"})
    assert resp.status_code == 422
    # legacy 工作区只读：切段一律 409
    (tmp_path / "storyline.md").write_text("旧五件套", encoding="utf-8")
    resp = client.put("/workspaces/ws1/writing/stage", json={"stage": "theme"})
    assert resp.status_code == 409
    resp = client.put("/workspaces/ws2/writing/stage", json={"stage": "theme"})
    assert resp.status_code == 404


# ── 段审循环推进端点（2026-10-27 自动模式卷纲连写 bug 修复）────────


def _seed_confirm_loop_book(ws: Path) -> None:
    """3 卷计划：第 1 卷 3 章写完，第 2 卷卷纲刚定稿（确认后应进章纲）。"""
    (ws / "卷纲").mkdir(exist_ok=True)
    (ws / "章纲").mkdir(exist_ok=True)
    (ws / "正文").mkdir(exist_ok=True)
    (ws / "主题.md").write_text(
        "# 主题 · 书\n> 状态：定稿\n\n## 一句话主题\nx\n\n## 约束级\n"
        "### 核心爽点\nx\n### 核心矛盾\nx\n### 核心爽点剧情\nx\n", encoding="utf-8",
    )
    (ws / "总纲.md").write_text(
        "# 总纲 · 书\n> 状态：定稿\n\n## 核心纲\n\n"
        "| 卷 | 小目标 | 地图 | BOSS | 关键节点 |\n|---|---|---|---|---|\n"
        "| 一 | 活下去 | 垃圾场 | 工头 | 捡神器 |\n"
        "| 二 | 保住小队 | 城区 | 连长 | 反杀 |\n"
        "| 三 | 夺回矿区 | 矿区 | 矿主 | 破局 |\n", encoding="utf-8",
    )
    vol = (
        "# 卷纲 · 第 1 卷 · 活下去\n> 状态：定稿\n\n| 卷末超大爽点 | 捡到首件神器 |\n|---|---|\n"
        "## 发展阶段\n阶段表\n## 爽点节奏表\n节奏表\n## 爽点质检表\n质检表\n## 卷末钩子\n钩子\n"
    )
    (ws / "卷纲" / "第01卷.md").write_text(
        vol.replace("\n节奏表\n", "\n本卷章号范围：1-3\n节奏表\n"), encoding="utf-8",
    )
    (ws / "卷纲" / "第02卷.md").write_text(
        vol.replace("\n节奏表\n", "\n本卷章号范围：4-6\n节奏表\n"), encoding="utf-8",
    )
    for n in (1, 2, 3):
        (ws / "章纲" / f"第{n:03d}章.md").write_text("# 章纲\n", encoding="utf-8")
        (ws / "正文" / f"第{n:03d}章完.md").write_text(
            f"# 第{n:03d}章 · 完\n> 状态：人修定稿\n", encoding="utf-8",
        )


def test_stage_advance_loop_roundtrip(client: TestClient, tmp_path: Path) -> None:
    """卷纲段审确认 → 进章纲（不连写下一卷）；trace_id 幂等重放 no-op。"""
    from contracts.writing_artifacts import load_writing_state

    _seed_confirm_loop_book(tmp_path)
    assert client.put("/workspaces/ws1/writing/stage", json={"stage": "volume"}).status_code == 200
    resp = client.post("/workspaces/ws1/writing/stage/advance", json={"trace_id": "tr-1"})
    assert resp.status_code == 200
    assert resp.json()["phase"] == "chapter"
    state = load_writing_state(tmp_path)
    assert state.confirmed_stage == "chapter"
    assert state.last_stage_advance["trace_id"] == "tr-1"
    assert state.last_stage_advance["from"] == "volume"
    # 同一待审 trace 重放（resume 网络重试）→ no-op，不二次跳段
    resp = client.post("/workspaces/ws1/writing/stage/advance", json={"trace_id": "tr-1"})
    assert resp.status_code == 200
    assert resp.json()["phase"] == "chapter"
    # 新一段的确认（新 trace）：章纲批已落盘 → 进正文
    (tmp_path / "章纲" / "第004章.md").write_text("# 章纲\n", encoding="utf-8")
    resp = client.post("/workspaces/ws1/writing/stage/advance", json={"trace_id": "tr-2"})
    assert resp.json()["phase"] == "text"


def test_stage_advance_rejects_legacy_and_missing(client: TestClient, tmp_path: Path) -> None:
    _seed_confirm_loop_book(tmp_path)
    assert client.post("/workspaces/ws2/writing/stage/advance", json={"trace_id": "t"}).status_code == 404
    (tmp_path / "storyline.md").write_text("旧五件套", encoding="utf-8")
    resp = client.post("/workspaces/ws1/writing/stage/advance", json={"trace_id": "t"})
    assert resp.status_code == 409


def test_stale_payoff_journal_does_not_block_advance(client: TestClient, tmp_path: Path) -> None:
    """REQ-20261030-200030：旧 payoff 日记不再拦截切段（防线已移除，定稿即放行）。"""
    import hashlib
    import json
    from contracts.writing_artifacts import load_writing_state

    _seed_confirm_loop_book(tmp_path)
    client.put("/workspaces/ws1/writing/stage", json={"stage": "volume"})
    original = (tmp_path / "卷纲/第02卷.md").read_text(encoding="utf-8")
    journal = {
        "schema_version": 1, "volumes": {"2": {
            "legacy_content_hash": hashlib.sha256(original.encode()).hexdigest(),
            "active_operation": {"operation_id": "failed-trace", "status": "blocked", "reason": "仍有清单外爽点"},
        }},
    }
    (tmp_path / ".writing/volume-payoff-checks.json").write_text(json.dumps(journal), encoding="utf-8")
    response = client.post("/workspaces/ws1/writing/stage/advance", json={"trace_id": "failed-trace"})
    assert response.status_code == 200
    assert load_writing_state(tmp_path).confirmed_stage == "chapter"
    assert (tmp_path / "卷纲/第02卷.md").read_text(encoding="utf-8") == original


@pytest.mark.parametrize("send_review_id", [True, False])
def test_same_trace_advances_each_review_through_volume_cycle(
    client: TestClient, tmp_path: Path, recorder: _FakeRecorder, send_review_id: bool,
) -> None:
    """兼容既有 trace 多次交审；新旧客户端都按当前工具调用推进。"""
    from contracts.writing_artifacts import load_writing_state

    _seed_confirm_loop_book(tmp_path)
    client.put("/workspaces/ws1/writing/stage", json={"stage": "volume"})

    def confirm(review_id: str, expected: str) -> None:
        recorder.await_review(review_id)
        payload = {"trace_id": "trace-loop"}
        if send_review_id:
            payload["review_id"] = review_id
        for _ in range(2):
            response = client.post("/workspaces/ws1/writing/stage/advance", json=payload)
            assert response.status_code == 200, response.text
            assert response.json()["phase"] == expected
        assert load_writing_state(tmp_path).last_stage_advance["review_id"] == review_id

    confirm("volume-2-ready", "chapter")
    (tmp_path / "章纲/第004章.md").write_text("# 章纲\n", encoding="utf-8")
    confirm("chapter-4-ready", "text")
    (tmp_path / "正文/第004章.md").write_text("# 正文\n", encoding="utf-8")
    confirm("text-4-stock-empty", "chapter")
    for chapter in (5, 6):
        (tmp_path / f"章纲/第{chapter:03d}章.md").write_text("# 章纲\n", encoding="utf-8")
    confirm("chapter-5-6-ready", "text")
    for chapter in (5, 6):
        (tmp_path / f"正文/第{chapter:03d}章.md").write_text("# 正文\n", encoding="utf-8")
    confirm("text-5-6-stock-empty", "chapter")
    confirm("all-volume-2-chapters-complete", "volume")
    state = load_writing_state(tmp_path)
    assert state.last_stage_advance == {
        "trace_id": "trace-loop", "review_id": "all-volume-2-chapters-complete",
        "from": "chapter", "to": "volume",
    }
    assert recorder.lookups
    assert all(path == str(tmp_path) for _, path in recorder.lookups)


def test_review_retry_after_sealing_does_not_advance_twice(
    client: TestClient, tmp_path: Path, recorder: _FakeRecorder,
) -> None:
    from contracts.writing_artifacts import load_writing_state

    _seed_confirm_loop_book(tmp_path)
    client.put("/workspaces/ws1/writing/stage", json={"stage": "volume"})
    recorder.await_review("volume-ready")
    payload = {"trace_id": "trace-loop", "review_id": "volume-ready"}
    assert client.post("/workspaces/ws1/writing/stage/advance", json=payload).json()["phase"] == "chapter"
    assert recorder.run is not None
    assert recorder.run.status == "cancelled"
    (tmp_path / "章纲/第004章.md").write_text("# 章纲\n", encoding="utf-8")

    response = client.post("/workspaces/ws1/writing/stage/advance", json=payload)
    assert response.status_code == 200
    assert response.json()["phase"] == "chapter"
    assert load_writing_state(tmp_path).last_stage_advance["review_id"] == "volume-ready"


def test_old_review_is_rejected_after_new_review_arrives(
    client: TestClient, tmp_path: Path, recorder: _FakeRecorder,
) -> None:
    from contracts.writing_artifacts import load_writing_state

    _seed_confirm_loop_book(tmp_path)
    client.put("/workspaces/ws1/writing/stage", json={"stage": "volume"})
    recorder.await_review("volume-ready")
    old_payload = {"trace_id": "trace-loop", "review_id": "volume-ready"}
    assert client.post("/workspaces/ws1/writing/stage/advance", json=old_payload).status_code == 200
    (tmp_path / "章纲/第004章.md").write_text("# 章纲\n", encoding="utf-8")
    recorder.await_review("chapter-ready")

    response = client.post("/workspaces/ws1/writing/stage/advance", json=old_payload)
    assert response.status_code == 409
    assert "审查已更新" in response.json()["detail"]
    assert load_writing_state(tmp_path).confirmed_stage == "chapter"
    response = client.post("/workspaces/ws1/writing/stage/advance", json={
        "trace_id": "trace-loop", "review_id": "chapter-ready",
    })
    assert response.status_code == 200
    assert response.json()["phase"] == "text"


@pytest.mark.parametrize("review_kind", ["style", "batch", "choice"])
def test_nonstage_interrupt_cannot_advance(
    client: TestClient, tmp_path: Path, recorder: _FakeRecorder, review_kind: str,
) -> None:
    from contracts.writing_artifacts import load_writing_state

    _seed_confirm_loop_book(tmp_path)
    client.put("/workspaces/ws1/writing/stage", json={"stage": "chapter"})
    recorder.await_review("not-stage", review_kind=review_kind, choice=review_kind == "choice")
    response = client.post("/workspaces/ws1/writing/stage/advance", json={
        "trace_id": "trace-loop", "review_id": "not-stage",
    })
    assert response.status_code == 409
    assert "不是段审查" in response.json()["detail"]
    assert load_writing_state(tmp_path).confirmed_stage == "chapter"


def test_review_from_another_workspace_cannot_advance(
    client: TestClient, tmp_path: Path, recorder: _FakeRecorder,
) -> None:
    from contracts.writing_artifacts import load_writing_state

    _seed_confirm_loop_book(tmp_path)
    client.put("/workspaces/ws1/writing/stage", json={"stage": "volume"})
    recorder.await_review("another-book-review")
    assert recorder.run is not None
    # recorder 的工作区参数只是查找提示，内存索引可能指向另一本书。
    recorder.run.workspace_path = str(tmp_path / "another-book")
    recorder.run.workspace_id = "ws-other"
    response = client.post("/workspaces/ws1/writing/stage/advance", json={
        "trace_id": "trace-loop", "review_id": "another-book-review",
    })
    assert response.status_code == 409
    assert load_writing_state(tmp_path).confirmed_stage == "volume"


def test_unknown_review_cannot_advance(client: TestClient, tmp_path: Path) -> None:
    from contracts.writing_artifacts import load_writing_state

    _seed_confirm_loop_book(tmp_path)
    client.put("/workspaces/ws1/writing/stage", json={"stage": "volume"})
    response = client.post("/workspaces/ws1/writing/stage/advance", json={
        "trace_id": "missing-trace", "review_id": "missing-review",
    })
    assert response.status_code == 409
    assert "待审任务不存在" in response.json()["detail"]
    assert load_writing_state(tmp_path).confirmed_stage == "volume"


def test_new_confirmation_requires_an_awaiting_run(
    client: TestClient, tmp_path: Path, recorder: _FakeRecorder,
) -> None:
    from contracts.writing_artifacts import load_writing_state

    _seed_confirm_loop_book(tmp_path)
    client.put("/workspaces/ws1/writing/stage", json={"stage": "volume"})
    recorder.await_review("volume-ready")
    assert recorder.run is not None
    recorder.run.status = "running"
    response = client.post("/workspaces/ws1/writing/stage/advance", json={
        "trace_id": "trace-loop", "review_id": "volume-ready",
    })
    assert response.status_code == 409
    assert "没有等待审查" in response.json()["detail"]
    assert load_writing_state(tmp_path).confirmed_stage == "volume"


@pytest.mark.parametrize("restarted", [False, True])
def test_cross_stage_confirmation_seals_run_and_isolates_new_conversation(
    client: TestClient, tmp_path: Path, monkeypatch, restarted: bool,
) -> None:
    from app.domains.writing.pipeline import route
    from app.platform.trace.recorder import STAGE_ADVANCED_MESSAGE, TraceRecorder
    from app.schemas.screenplay import ThreadSummary
    from contracts.writing_artifacts import load_writing_state, save_writing_state

    _seed_confirm_loop_book(tmp_path)
    thread = ThreadSummary(
        thread_id="t1", workspace_id="ws1", session_name="旧段",
        workspace_path=str(tmp_path), created_at="2026-10-29T00:00:00Z",
        updated_at="2026-10-29T00:00:00Z",
    )
    monkeypatch.setattr("app.platform.trace.recorder._notify_evolution", lambda *a, **kw: None)
    recorder = TraceRecorder()
    trace = recorder.create_run(thread, "screenplay.generate.stream")
    recorder.append_event(trace.trace_id, {
        "type": "tool_start", "status": "running", "source": "middleware",
        "tool_name": "request_review", "tool_call_id": "volume-ready",
        "tool_args": {"question": "卷纲已完成", "review_kind": "stage"},
    })
    recorder.record_hitl(trace.trace_id, "author", phase="tool", state="requested",
                         tool_call_id="volume-ready")
    recorder.await_input_run(thread, trace.trace_id)
    state = load_writing_state(tmp_path)
    state.confirmed_stage = "volume"
    state.active_traces = {"author": trace.trace_id, "other": "unrelated-trace"}
    state.active_threads = {"author": "t1:author:1"}
    state.conversation_forks = {"t1": 1}
    save_writing_state(tmp_path, state)
    if restarted:
        recorder = TraceRecorder()
    monkeypatch.setattr(ws_router, "get_trace_recorder", lambda: recorder)
    payload = {"trace_id": trace.trace_id, "review_id": "volume-ready"}

    for _ in range(2):
        response = client.post("/workspaces/ws1/writing/stage/advance", json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["phase"] == "chapter"
        assert response.json()["awaiting"] == {"other": "unrelated-trace"}
    # 旧客户端不传 review_id，封存后重试也不重新推进。
    assert client.post("/workspaces/ws1/writing/stage/advance", json={
        "trace_id": trace.trace_id,
    }).status_code == 200
    run = recorder.find_run_by_trace_id(trace.trace_id, workspace_path=str(tmp_path))
    assert run is not None and run.status == "cancelled"
    assert run.error == STAGE_ADVANCED_MESSAGE
    assert run.manifest is not None and run.trace_phase == "sealed"
    events = recorder.read_trace_events(trace.trace_id)
    assert sum(event.type == "run_cancelled" for event in events) == 1
    assert recorder.get_active_queue(trace.trace_id) is None
    state = load_writing_state(tmp_path)
    assert state.active_traces == {"other": "unrelated-trace"}
    assert state.active_threads == {"author": "t1:author:1"}
    next_route = route(tmp_path, "new-base-thread", resume_trace=None)
    assert next_route.new_session is True
    assert next_route.thread_id == "new-base-thread:author:1"
    assert next_route.thread_id != state.active_threads["author"]
    assert next_route.resume_degraded is False
    # 下一轮已经登记同一 agent 时，旧确认重试不能清掉新 trace。
    state.active_traces["author"] = "new-stage-trace"
    save_writing_state(tmp_path, state)
    assert client.post("/workspaces/ws1/writing/stage/advance", json=payload).status_code == 200
    assert load_writing_state(tmp_path).active_traces["author"] == "new-stage-trace"


def test_same_stage_confirmation_keeps_review_resumable(
    client: TestClient, tmp_path: Path, recorder: _FakeRecorder,
) -> None:
    from contracts.writing_artifacts import load_writing_state, save_writing_state

    _seed_confirm_loop_book(tmp_path)
    recorder.await_review("chapter-not-finished")
    state = load_writing_state(tmp_path)
    state.confirmed_stage = "chapter"
    state.active_traces["author"] = "trace-loop"
    state.active_threads["author"] = "t1:author:1"
    save_writing_state(tmp_path, state)
    for _ in range(2):
        response = client.post("/workspaces/ws1/writing/stage/advance", json={
            "trace_id": "trace-loop", "review_id": "chapter-not-finished",
        })
        assert response.status_code == 200
        assert response.json()["phase"] == "chapter"
    assert recorder.run.status == "awaiting_input"
    assert recorder.cancelled == []
    assert load_writing_state(tmp_path).active_traces == {"author": "trace-loop"}


def test_stage_advance_retry_repairs_failed_seal_without_advancing_again(
    client: TestClient, tmp_path: Path, recorder: _FakeRecorder, monkeypatch,
) -> None:
    from contracts.writing_artifacts import load_writing_state, save_writing_state

    _seed_confirm_loop_book(tmp_path)
    recorder.await_review("volume-ready")
    state = load_writing_state(tmp_path)
    state.confirmed_stage = "volume"
    state.active_traces["author"] = "trace-loop"
    save_writing_state(tmp_path, state)
    cancel = recorder.cancel_awaiting_run

    def fail_seal(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(recorder, "cancel_awaiting_run", fail_seal)
    payload = {"trace_id": "trace-loop", "review_id": "volume-ready"}
    response = client.post("/workspaces/ws1/writing/stage/advance", json=payload)
    assert response.status_code == 503
    assert load_writing_state(tmp_path).confirmed_stage == "chapter"
    assert load_writing_state(tmp_path).active_traces == {"author": "trace-loop"}
    (tmp_path / "章纲/第004章.md").write_text("# 章纲\n", encoding="utf-8")
    monkeypatch.setattr(recorder, "cancel_awaiting_run", cancel)
    response = client.post("/workspaces/ws1/writing/stage/advance", json=payload)
    assert response.status_code == 200
    assert response.json()["phase"] == "chapter"
    assert load_writing_state(tmp_path).active_traces == {}
    assert recorder.cancelled == [("trace-loop", "stage_advanced")]


def test_stage_advance_retry_cannot_seal_another_owners_thread(
    client: TestClient, tmp_path: Path, recorder: _FakeRecorder,
) -> None:
    from contracts.writing_artifacts import load_writing_state, save_writing_state

    _seed_confirm_loop_book(tmp_path)
    recorder.await_review("volume-ready")
    recorder.run.thread_id = "someone-elses-thread"
    state = load_writing_state(tmp_path)
    state.confirmed_stage = "chapter"
    state.last_stage_advance = {
        "trace_id": "trace-loop", "review_id": "volume-ready", "from": "volume", "to": "chapter",
    }
    save_writing_state(tmp_path, state)
    response = client.post("/workspaces/ws1/writing/stage/advance", json={
        "trace_id": "trace-loop", "review_id": "volume-ready",
    })
    assert response.status_code == 409
    assert recorder.cancelled == []
    assert recorder.run.status == "awaiting_input"


def test_outline_tree_includes_character_cards(client: TestClient, tmp_path: Path) -> None:
    """人物卡随产物全集下发（REQ-20261031-183257 客户端人物模块数据源）。

    合法人名卡进列表（主角卡排组内首位）；非法名文件不下发。
    """
    _seed_book(tmp_path)
    ws = tmp_path
    ch = ws / "人物"
    ch.mkdir()
    (ch / "主角.md").write_text("# 人物卡 · 主角 · 林晚\n", encoding="utf-8")
    (ch / "老陈.md").write_text("# 人物卡 · 重要配角 · 老陈\n", encoding="utf-8")
    (ch / "bad:name.md").write_text("x", encoding="utf-8")

    resp = client.get("/workspaces/ws1/outline")
    assert resp.status_code == 200
    rels = [f["rel"] for f in resp.json()["files"]]
    # 五段产物在前（顺序不变），人物卡追加在尾部（人名字典序，主角自然居首）
    assert rels[:6] == [
        "主题.md", "总纲.md", "文风.md",
        "卷纲/第01卷.md", "章纲/第001章.md", "正文/第001章开局.md",
    ]
    assert rels[6:] == ["人物/主角.md", "人物/老陈.md"]
    assert all("bad:" not in r for r in rels)
