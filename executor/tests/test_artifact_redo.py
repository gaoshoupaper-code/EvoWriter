"""定稿产物删除重做测试（REQ-20261030-211446 全量开放：六类产物）。

跑法（在 executor 目录）：python -m pytest tests/test_artifact_redo.py -v

锁定：
  - 资格矩阵（从下往上不级联）：主题需无总纲；总纲需下游目录全空；
    卷纲按「本卷章号范围」判定、范围解析失败兜底章纲全空（fail-safe）；
    章纲需该章正文不存在；正文/文风随时可删（文风特例 DEC-002）
  - 任意位置可删（DEC-003）：中间卷/章可删，不要求最末
  - 删除动作：文件移入 .writing/trash、标注清除、段指针回落矩阵
  - 批量端点：逐个独立校验、部分成功（AC-006）
  - 回收站端点：列表倒序、恢复、原位冲突 409、404（AC-007）
  - 范围外 rel 与不存在文件拒绝；legacy 409（端点）
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import current_user
from app.domains.writing.artifact_redo import (
    delete_artifact_for_redo,
    redo_eligibility,
    redo_target_stage,
)
from app.routers import workspaces as ws_router


class _FakeStore:
    def __init__(self, workspace_path: Path) -> None:
        self._ws = SimpleNamespace(workspace_path=str(workspace_path))

    def get_workspace(self, owner_id: str, workspace_id: str):
        return self._ws if workspace_id == "ws1" else None


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    app = FastAPI()
    app.include_router(ws_router.router)
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(user_id="u1")
    monkeypatch.setattr(ws_router, "get_thread_store", lambda: _FakeStore(tmp_path))
    return TestClient(app)


def _seed(
    ws: Path,
    *,
    theme: bool = False,
    master: bool = False,
    style: bool = False,
    volumes: dict[int, tuple[int, int] | None] | None = None,
    chapters: list[int] | None = None,
    texts: list[int] | None = None,
) -> None:
    """六类产物种子：volumes 卷号→(起,止) 章号范围（None=不写范围行）。"""
    ws.mkdir(parents=True, exist_ok=True)
    if theme:
        (ws / "主题.md").write_text("# 主题\n> 状态：定稿\n\n## 一句话主题\n爽\n", encoding="utf-8")
    if master:
        (ws / "总纲.md").write_text("# 总纲\n> 状态：定稿\n\n核心纲内容\n", encoding="utf-8")
    if style:
        (ws / "文风.md").write_text("# 文风\n> 状态：定稿\n", encoding="utf-8")
    if volumes:
        (ws / "卷纲").mkdir(exist_ok=True)
        for v, rng in volumes.items():
            line = f"本卷章号范围：第{rng[0]}-{rng[1]}章\n" if rng else ""
            (ws / "卷纲" / f"第{v:02d}卷.md").write_text(
                f"# 卷纲 · 第 {v} 卷\n> 状态：定稿\n\n{line}", encoding="utf-8",
            )
    if chapters:
        (ws / "章纲").mkdir(exist_ok=True)
        for c in chapters:
            (ws / "章纲" / f"第{c:03d}章.md").write_text(f"# 章纲 · 第{c:03d}章\n", encoding="utf-8")
    if texts:
        (ws / "正文").mkdir(exist_ok=True)
        for c in texts:
            (ws / "正文" / f"第{c:03d}章.md").write_text(f"# 第{c:03d}章 · 试写\n> 状态：定稿\n", encoding="utf-8")


def _set_stage(ws: Path, stage: str) -> None:
    from contracts.writing_artifacts import load_writing_state, save_writing_state

    state = load_writing_state(ws)
    state.confirmed_stage = stage
    state.stage = stage
    save_writing_state(ws, state)


# ── 域函数：目标段映射 ─────────────────────────────────────


def test_redo_target_stage_mapping():
    assert redo_target_stage("主题.md") == "theme"
    assert redo_target_stage("总纲.md") == "outline"
    assert redo_target_stage("文风.md") == "chapter"
    assert redo_target_stage("卷纲/第02卷.md") == "volume"
    assert redo_target_stage("章纲/第005章.md") == "chapter"
    assert redo_target_stage("正文/第005章重写这章.md") == "text"
    assert redo_target_stage("正文/第005章.md") == "text"
    assert redo_target_stage("设定/角色.md") is None
    assert redo_target_stage("总纲-修订草稿.md") is None


# ── 域函数：资格矩阵（FR-002）─────────────────────────────


def test_theme_eligibility(tmp_path: Path):
    _seed(tmp_path, master=True)
    ok, reason = redo_eligibility(tmp_path, "主题.md")
    assert not ok and "总纲" in reason
    ws2 = tmp_path / "b"
    _seed(ws2, theme=True)
    assert redo_eligibility(ws2, "主题.md") == (True, "")


def test_master_not_eligible_with_any_downstream(tmp_path: Path):
    _seed(tmp_path, master=True, volumes={1: None})
    ok, reason = redo_eligibility(tmp_path, "总纲.md")
    assert not ok and "最下游" in reason

    ws2 = tmp_path / "b"
    _seed(ws2, master=True, chapters=[1])
    ok, _ = redo_eligibility(ws2, "总纲.md")
    assert not ok


def test_master_eligible_when_downstream_empty(tmp_path: Path):
    _seed(tmp_path, master=True, style=True)  # 文风不是总纲的直接下游，不拦截
    assert redo_eligibility(tmp_path, "总纲.md") == (True, "")


def test_volume_eligibility_by_own_range(tmp_path: Path):
    """卷级判定只看本卷章号范围内的章纲（DEC-003：中间卷可删）。"""
    _seed(tmp_path, volumes={1: (1, 10), 2: (11, 20)}, chapters=[15])
    # 章纲 15 落在第 2 卷范围 → 第 2 卷拒、第 1 卷放行（即使第 1 卷不是最大卷号）
    ok, reason = redo_eligibility(tmp_path, "卷纲/第02卷.md")
    assert not ok and "章纲" in reason
    assert redo_eligibility(tmp_path, "卷纲/第01卷.md") == (True, "")


def test_volume_range_parse_failure_fallback(tmp_path: Path):
    """范围解析失败 → fail-safe：章纲目录非空一律拒。"""
    _seed(tmp_path, volumes={1: None}, chapters=[99])
    ok, reason = redo_eligibility(tmp_path, "卷纲/第01卷.md")
    assert not ok and "章纲" in reason
    ws2 = tmp_path / "b"
    _seed(ws2, volumes={1: None})
    assert redo_eligibility(ws2, "卷纲/第01卷.md") == (True, "")


def test_chapter_eligibility(tmp_path: Path):
    _seed(tmp_path, chapters=[5], texts=[5])
    ok, reason = redo_eligibility(tmp_path, "章纲/第005章.md")
    assert not ok and "正文" in reason
    ws2 = tmp_path / "b"
    _seed(ws2, chapters=[5], texts=[6])  # 第 5 章正文不存在（文件名带章名也算占位）
    assert redo_eligibility(ws2, "章纲/第005章.md") == (True, "")


def test_text_and_style_always_eligible(tmp_path: Path):
    """正文是最下游、文风是特例（DEC-002）：全产物在场也可删。"""
    _seed(
        tmp_path,
        theme=True, master=True, style=True,
        volumes={1: (1, 3)}, chapters=[1, 2, 3], texts=[1, 2, 3],
    )
    assert redo_eligibility(tmp_path, "正文/第003章.md") == (True, "")
    assert redo_eligibility(tmp_path, "文风.md") == (True, "")


def test_delete_rejects_out_of_scope_and_missing(tmp_path: Path):
    _seed(tmp_path, volumes={1: None})
    with pytest.raises(ValueError, match="只支持"):
        delete_artifact_for_redo(tmp_path, "设定/角色.md")
    with pytest.raises(ValueError, match="不存在"):
        delete_artifact_for_redo(tmp_path, "卷纲/第09卷.md")


# ── 域函数：删除动作（AC-001/003/005）─────────────────────


def test_delete_midchapter_progress_recognizes_gap(tmp_path: Path):
    """AC-004：删中间章留空档后，进度推导按「缺失即待补」识别。"""
    from contracts.writing_artifacts import derive_stage, scan_writing_progress

    # 种子需过硬定稿闸（结构校验），否则 derive 停在前段
    ws = tmp_path
    _seed(ws, master=True, volumes={1: (1, 80)}, chapters=list(range(1, 81)), texts=list(range(1, 81)))
    (ws / "主题.md").write_text(
        "# 主题\n> 状态：定稿\n\n## 一句话主题\n废柴觉醒\n\n## 核心爽点\n打脸\n\n## 核心矛盾\n阶层压制\n\n## 核心爽点剧情\n逐级反杀\n",
        encoding="utf-8",
    )
    (ws / "总纲.md").write_text(
        "# 总纲\n> 状态：定稿\n\n## 核心纲\n\n| 卷 | 主导矛盾 | 卷末爽点 |\n|---|---|---|\n| 1 | 觉醒 | 初胜 |\n",
        encoding="utf-8",
    )
    assert delete_artifact_for_redo(ws, "正文/第050章.md")["status"] == "ok"
    # 章纲在而正文缺 50 → 该补正文（text 段）
    assert derive_stage(scan_writing_progress(ws)) == "text"
    # 连章纲 50 也删 → 停留章纲段补第 50 章章纲；51-80 编号不动
    assert delete_artifact_for_redo(ws, "章纲/第050章.md")["status"] == "ok"
    assert derive_stage(scan_writing_progress(ws)) == "chapter"
    assert (ws / "章纲" / "第051章.md").is_file()
    assert (ws / "正文" / "第080章.md").is_file()
    # 删中间卷（共 5 卷，第 2 卷范围内无章纲）不影响后续卷
    _seed(ws, volumes={2: (81, 100), 3: None, 4: None, 5: None})
    assert delete_artifact_for_redo(ws, "卷纲/第02卷.md")["status"] == "ok"
    assert (ws / "卷纲" / "第03卷.md").is_file()


def test_delete_chain_all_six_kinds(tmp_path: Path):
    """从最下游删到最上游，六类全链路走通；产物树清空、trash 齐。"""
    _seed(
        tmp_path,
        theme=True, master=True, style=True,
        volumes={1: (1, 3)}, chapters=[1, 2, 3], texts=[1, 2, 3],
    )
    order = [
        "正文/第003章.md", "正文/第002章.md", "正文/第001章.md",
        "章纲/第003章.md", "章纲/第002章.md", "章纲/第001章.md",
        "文风.md",
        "卷纲/第01卷.md",
        "总纲.md",
        "主题.md",
    ]
    trash_rels = []
    for rel in order:
        result = delete_artifact_for_redo(tmp_path, rel)
        assert result["status"] == "ok" and result["deleted"] == rel
        assert not (tmp_path / rel).exists()
        assert (tmp_path / result["trash_rel"]).is_file()
        trash_rels.append(result["trash_rel"])
    assert len(trash_rels) == len(order) == 10
    for d in ("卷纲", "章纲", "正文"):
        assert not any((tmp_path / d).glob("*.md"))


def test_delete_master_moves_trash_and_resets_stage(tmp_path: Path):
    from app.domains.writing.annotations import create_annotation, load_annotations
    from app.schemas.screenplay import AnnotationCreateRequest

    _seed(tmp_path, master=True)
    _set_stage(tmp_path, "volume")
    create_annotation(tmp_path, AnnotationCreateRequest(
        artifact_rel="总纲.md", label="abstract", quote="核心纲内容",
    ))

    result = delete_artifact_for_redo(tmp_path, "总纲.md")
    assert result["status"] == "ok"
    assert not (tmp_path / "总纲.md").exists()
    assert ".writing/trash" in result["trash_rel"] and (tmp_path / result["trash_rel"]).is_file()
    assert result["removed_annotations"] == 1
    assert load_annotations(tmp_path) == []
    assert result["stage_reset_to"] == "outline"


def test_pointer_reset_matrix(tmp_path: Path):
    from contracts.writing_artifacts import load_writing_state

    # 指针在下游：删章纲 → 回 chapter
    ws = tmp_path / "a"
    _seed(ws, chapters=[5])
    _set_stage(ws, "text")
    assert delete_artifact_for_redo(ws, "章纲/第005章.md")["stage_reset_to"] == "chapter"
    assert load_writing_state(ws).confirmed_stage == "chapter"

    # 删正文 → 不回落（text 已是最下游段）
    ws = tmp_path / "b"
    _seed(ws, chapters=[5], texts=[5])
    _set_stage(ws, "text")
    assert delete_artifact_for_redo(ws, "正文/第005章.md")["stage_reset_to"] is None
    assert load_writing_state(ws).confirmed_stage == "text"

    # 删文风（特例）→ 指针在 text 时回 chapter
    ws = tmp_path / "c"
    _seed(ws, style=True)
    _set_stage(ws, "text")
    assert delete_artifact_for_redo(ws, "文风.md")["stage_reset_to"] == "chapter"

    # 删主题 → 指针在 outline 时回 theme
    ws = tmp_path / "d"
    _seed(ws, theme=True)
    _set_stage(ws, "outline")
    assert delete_artifact_for_redo(ws, "主题.md")["stage_reset_to"] == "theme"

    # 指针在上游：删正文不动指针
    ws = tmp_path / "e"
    _seed(ws, chapters=[5], texts=[5])
    _set_stage(ws, "volume")
    assert delete_artifact_for_redo(ws, "正文/第005章.md")["stage_reset_to"] is None
    assert load_writing_state(ws).confirmed_stage == "volume"


# ── 端点 ──────────────────────────────────────────────────


def test_endpoint_delete_and_legacy_409(client: TestClient, tmp_path: Path):
    _seed(tmp_path, volumes={1: None})
    resp = client.post("/workspaces/ws1/artifact/delete", json={"rel": "卷纲/第01卷.md"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] == "卷纲/第01卷.md"
    assert body["pipeline"]["kind"] == "five"

    # 资格不符（该卷范围内有章纲）→ 409 带中文原因
    _seed(tmp_path, volumes={1: (1, 10)}, chapters=[3])
    resp = client.post("/workspaces/ws1/artifact/delete", json={"rel": "卷纲/第01卷.md"})
    assert resp.status_code == 409
    assert "章纲" in resp.json()["detail"]


def test_endpoint_legacy_guard(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "outline").mkdir()
    (tmp_path / "outline" / "世界观.md").write_text("三步制", encoding="utf-8")
    app = FastAPI()
    app.include_router(ws_router.router)
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(user_id="u1")
    monkeypatch.setattr(ws_router, "get_thread_store", lambda: _FakeStore(tmp_path))
    client = TestClient(app)
    resp = client.post("/workspaces/ws1/artifact/delete", json={"rel": "总纲.md"})
    assert resp.status_code == 409


# ── 端点：批量删除（FR-005 / AC-006）──────────────────────


def test_endpoint_batch_partial_success(client: TestClient, tmp_path: Path):
    _seed(tmp_path, chapters=[1, 2, 3], texts=[1, 2, 3])
    rels = [
        "正文/第001章.md", "正文/第002章.md",
        "正文/第999章.md",   # 不存在 → 该条失败
        "章纲/第003章.md",   # 第 3 章正文仍在 → 该条失败
    ]
    resp = client.post("/workspaces/ws1/artifact/delete-batch", json={"rels": rels})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted_count"] == 2
    assert len(body["results"]) == 4
    failed = {r["rel"]: r["reason"] for r in body["results"] if r["status"] == "failed"}
    assert "不存在" in failed["正文/第999章.md"]
    assert "正文" in failed["章纲/第003章.md"]
    assert not (tmp_path / "正文/第001章.md").exists()
    assert not (tmp_path / "正文/第002章.md").exists()
    assert (tmp_path / "正文/第003章.md").is_file()
    assert body["pipeline"]["kind"] == "five"


def test_endpoint_batch_empty_rels_rejected(client: TestClient, tmp_path: Path):
    resp = client.post("/workspaces/ws1/artifact/delete-batch", json={"rels": []})
    assert resp.status_code == 422


# ── 端点：回收站列表与恢复（FR-006 / AC-007）───────────────


def test_traversal_rels_rejected_everywhere(tmp_path: Path):
    """P0 修复回归：经标题携带穿越段/反斜杠/双下划线的 rel 全链路拒绝。"""
    _seed(tmp_path, master=True, chapters=[1], texts=[1])
    # 正向斜杠穿越（指向工作区内其他文件）——parse 层不识别 → 只支持
    with pytest.raises(ValueError, match="只支持"):
        delete_artifact_for_redo(tmp_path, "正文/第001章/../../总纲.md")
    # 反斜杠变体（Windows 语义 / Linux 字面量）两侧都被标题组排除
    with pytest.raises(ValueError, match="只支持"):
        delete_artifact_for_redo(tmp_path, "正文/第001章..\\..\\总纲.md")
    # 文件名含 __ → 删除后回收站无法还原，宁拒删
    (tmp_path / "正文" / "第005章大__战.md").write_text("# 第005章", encoding="utf-8")
    with pytest.raises(ValueError, match="__"):
        delete_artifact_for_redo(tmp_path, "正文/第005章大__战.md")
    assert (tmp_path / "正文" / "第005章大__战.md").is_file()
    # 关键资产分毫未动
    assert (tmp_path / "总纲.md").is_file()
    # 恢复端：trash_rel 带反斜杠（Windows ..\..\ 变体）→ 非法路径
    from app.domains.writing.artifact_trash import restore_trash_item
    with pytest.raises(ValueError, match="非法"):
        restore_trash_item(tmp_path, ".writing/trash/..\\..\\总纲.md")


def test_endpoint_batch_oserror_keeps_partial_success(client: TestClient, tmp_path: Path, monkeypatch):
    """批量条目 OSError（并发删除/文件系统异常）按条记失败，不打断整批。"""
    _seed(tmp_path, texts=[1, 2])

    real_move = __import__("shutil").move

    def _boom(src, dst, **kw):
        if str(src).endswith("第001章.md"):
            raise OSError("simulated concurrent removal")
        return real_move(src, dst, **kw)

    monkeypatch.setattr("app.domains.writing.artifact_redo.shutil.move", _boom)
    resp = client.post(
        "/workspaces/ws1/artifact/delete-batch",
        json={"rels": ["正文/第001章.md", "正文/第002章.md"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted_count"] == 1
    by_rel = {r["rel"]: r for r in body["results"]}
    assert by_rel["正文/第001章.md"]["status"] == "failed"
    assert "并发" in by_rel["正文/第001章.md"]["reason"]
    assert by_rel["正文/第002章.md"]["status"] == "ok"


def test_endpoint_legacy_guards_batch_and_trash(tmp_path: Path, monkeypatch) -> None:
    """legacy 工作区对批量删除/回收站/恢复三个新端点同样 409。"""
    (tmp_path / "outline").mkdir()
    (tmp_path / "outline" / "世界观.md").write_text("三步制", encoding="utf-8")
    app = FastAPI()
    app.include_router(ws_router.router)
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(user_id="u1")
    monkeypatch.setattr(ws_router, "get_thread_store", lambda: _FakeStore(tmp_path))
    client = TestClient(app)
    assert client.post("/workspaces/ws1/artifact/delete-batch", json={"rels": ["总纲.md"]}).status_code == 409
    assert client.get("/workspaces/ws1/trash").status_code == 409
    assert client.post("/workspaces/ws1/trash/restore", json={"trash_rel": ".writing/trash/x.md"}).status_code == 409


def test_endpoint_trash_list_and_restore(client: TestClient, tmp_path: Path):
    _seed(tmp_path, chapters=[1], texts=[1])
    assert client.post("/workspaces/ws1/artifact/delete", json={"rel": "正文/第001章.md"}).status_code == 200
    assert client.post("/workspaces/ws1/artifact/delete", json={"rel": "章纲/第001章.md"}).status_code == 200

    resp = client.get("/workspaces/ws1/trash")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    # 按删除时间倒序：后删的章纲在前
    assert items[0]["original_rel"] == "章纲/第001章.md"
    assert items[0]["kind_label"] == "章纲"
    assert items[1]["original_rel"] == "正文/第001章.md"
    assert items[1]["kind_label"] == "正文"
    assert items[0]["deleted_at"] >= items[1]["deleted_at"]

    # 恢复章纲 → 回原位、指针不动、列表减少
    _set_stage(tmp_path, "text")
    resp = client.post("/workspaces/ws1/trash/restore", json={"trash_rel": items[0]["trash_rel"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["restored"] == "章纲/第001章.md"
    assert (tmp_path / "章纲/第001章.md").is_file()
    from contracts.writing_artifacts import load_writing_state
    assert load_writing_state(tmp_path).confirmed_stage == "text"
    assert len(client.get("/workspaces/ws1/trash").json()["items"]) == 1

    # 再恢复同一条 → 404
    resp = client.post("/workspaces/ws1/trash/restore", json={"trash_rel": items[0]["trash_rel"]})
    assert resp.status_code == 404


def test_endpoint_trash_restore_conflict(client: TestClient, tmp_path: Path):
    _seed(tmp_path, texts=[1])
    client.post("/workspaces/ws1/artifact/delete", json={"rel": "正文/第001章.md"})
    # 原位已有新生成的同名文件 → 拒绝且不覆盖
    (tmp_path / "正文/第001章.md").write_text("# 第001章 · 新稿\n", encoding="utf-8")
    items = client.get("/workspaces/ws1/trash").json()["items"]
    resp = client.post("/workspaces/ws1/trash/restore", json={"trash_rel": items[0]["trash_rel"]})
    assert resp.status_code == 409
    assert "原位" in resp.json()["detail"] or "已有" in resp.json()["detail"]
    assert "新稿" in (tmp_path / "正文/第001章.md").read_text(encoding="utf-8")


def test_trash_old_stamp_format_still_restorable(client: TestClient, tmp_path: Path):
    """2026-10 旧格式时间戳（无微秒）的暂存文件同样可列出、可恢复。"""
    trash_dir = tmp_path / ".writing/trash"
    trash_dir.mkdir(parents=True)
    (trash_dir / "正文__第001章.md.20261028-120000.md").write_text(
        "# 第001章 · 旧稿\n", encoding="utf-8",
    )
    items = client.get("/workspaces/ws1/trash").json()["items"]
    assert len(items) == 1
    assert items[0]["original_rel"] == "正文/第001章.md"
    assert items[0]["deleted_at"] == "2026-10-28 12:00:00"
    resp = client.post("/workspaces/ws1/trash/restore", json={"trash_rel": items[0]["trash_rel"]})
    assert resp.status_code == 200
    assert (tmp_path / "正文/第001章.md").is_file()


def test_trash_rejects_traversal_and_unknown_names(client: TestClient, tmp_path: Path):
    _seed(tmp_path, master=True)
    resp = client.post("/workspaces/ws1/trash/restore", json={"trash_rel": "../总纲.md"})
    assert resp.status_code in (404, 409, 422)
    # 名字解析不出五段产物的暂存文件不列出、不可恢复（fail-safe）
    trash_dir = tmp_path / ".writing/trash"
    trash_dir.mkdir(parents=True)
    (trash_dir / "设定__角色.md.20261028-120000.md").write_text("x", encoding="utf-8")
    assert client.get("/workspaces/ws1/trash").json()["items"] == []
