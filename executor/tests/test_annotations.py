"""产物划词标注测试（REQ-20261027-213246 FR-001/002/004/005 服务侧）。

跑法（在 executor 目录）：python -m pytest tests/test_annotations.py -v

锁定：
  - 锚点归一匹配：渲染文本 quote 对 markdown 源文件（强调/链接/标题/表格）可命中
  - verify_anchor：unique / not_found / ambiguous（前后文消歧）
  - 三态流转：dispatched→resolved、非 dispatched→invalid、终态不动、绝不自动删
  - dispatch 组装：标签默认指令＋意见＋原文；发出前复验剔除失效；无待处理报错
  - 端点：CRUD / 重框选复位 / 批量清空 / legacy 409 / 范围外 rel 拒绝
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import current_user
from app.domains.writing.annotations import (
    ANNOTATIONS_FILE,
    LABEL_ABSTRACT,
    LABEL_CONTRADICTION,
    LABEL_CUSTOM,
    create_annotation,
    clear_annotations,
    dispatch_annotations,
    load_annotations,
    normalize_text,
    reverify_annotations,
    save_annotations,
    verify_anchor,
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


# 关键句在总纲内出现两次（歧义用例的前提）；核心纲表格供「表格命中」用例
_MASTER = (
    "# 总纲 · 废土书\n> 状态：定稿｜2026-10-01\n\n"
    "## 核心纲\n\n| 卷 | 小目标 | BOSS |\n|---|---|---|\n| 一 | 活下去 | 恶霸 |\n\n"
    "他一挥手，法则之力倾泻而下，把追兵尽数轰飞。\n\n第二段独立文字。\n\n"
    "回看当天：他一挥手，法则之力倾泻而下，把追兵尽数轰飞。\n"
)


def _seed_five_book(ws: Path) -> None:
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "总纲.md").write_text(_MASTER, encoding="utf-8")
    (ws / "文风.md").write_text("# 文风\n> 状态：风格卡已定\n\n短句快节奏。\n", encoding="utf-8")
    (ws / "卷纲").mkdir()
    (ws / "卷纲" / "第01卷.md").write_text(
        "# 卷纲 · 第 1 卷\n> 状态：定稿\n\n**关键设定**：他一挥手，法则之力倾泻而下，把追兵尽数轰飞。\n",
        encoding="utf-8",
    )


# ── 归一与锚点校验 ─────────────────────────────────────────


def test_normalize_strips_markdown_syntax():
    """渲染文本与源文件的归一结果一致（强调/链接/标题/引用/列表/表格线）。"""
    src = (
        "# 标题\n\n> 引用行\n\n- **加粗** 和 [链接文字](http://x) `code`\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |"
    )
    rendered = "标题\n引用行\n加粗 和 链接文字 code\na b\n1 2"
    assert normalize_text(src) == normalize_text(rendered)


def test_verify_anchor_unique_not_found_ambiguous():
    text = "第一处重复文字后面有甲。中间别的。第二处重复文字后面有乙。"
    assert verify_anchor(text, "中间别的", "", "") == "unique"
    assert verify_anchor(text, "不存在的文字", "", "") == "not_found"
    assert verify_anchor(text, "重复文字", "", "") == "ambiguous"
    # 前后文消歧：带上「后面有乙」可定位第二处
    assert verify_anchor(text, "重复文字", "第二处", "后面有乙") == "unique"


def test_verify_anchor_matches_bold_source():
    """渲染文本 quote 命中带 ** 强调的源文件（表头/强调语法不挡匹配）。"""
    volume = "**关键设定**：他一挥手，法则之力倾泻而下，把追兵尽数轰飞。"
    quote = "他一挥手，法则之力倾泻而下，把追兵尽数轰飞。"
    assert verify_anchor(volume, quote, "关键设定：", "") == "unique"
    # 表格单元格文本可命中
    assert verify_anchor(_MASTER, "活下去", "卷", "恶霸") == "unique"


# ── 域函数：创建/复验/派发 ─────────────────────────────────


def _req(rel: str, quote: str, label: str = LABEL_ABSTRACT, **kw):
    from app.schemas.screenplay import AnnotationCreateRequest

    return AnnotationCreateRequest(artifact_rel=rel, label=label, quote=quote, **kw)


def test_create_annotation_persists_pending(tmp_path: Path):
    _seed_five_book(tmp_path)
    ann = create_annotation(tmp_path, _req("总纲.md", "第二段独立文字", note="太虚"))
    assert ann.status == "pending" and ann.dispatched is False
    stored = load_annotations(tmp_path)
    assert [a.id for a in stored] == [ann.id]
    assert (tmp_path / ANNOTATIONS_FILE).is_file()


def test_create_custom_without_label_name(tmp_path: Path):
    """自定义免标签名（修订 2026-10-27）：只写意见即可；意见标签名全空才拒。"""
    _seed_five_book(tmp_path)
    ann = create_annotation(
        tmp_path, _req("总纲.md", "第二段独立文字", label=LABEL_CUSTOM, note="这句莫名其妙")
    )
    assert ann.label == "custom" and ann.custom_label is None and ann.note == "这句莫名其妙"
    with pytest.raises(ValueError, match="意见"):
        create_annotation(tmp_path, _req("总纲.md", "第二段独立文字", label=LABEL_CUSTOM))


def test_create_rejects_ambiguous_and_missing(tmp_path: Path):
    _seed_five_book(tmp_path)
    dup = "他一挥手，法则之力倾泻而下，把追兵尽数轰飞。"
    with pytest.raises(ValueError, match="出现多次"):
        create_annotation(tmp_path, _req("总纲.md", dup))
    with pytest.raises(ValueError, match="找不到"):
        create_annotation(tmp_path, _req("总纲.md", "不存在的句子"))


def test_reverify_state_transitions(tmp_path: Path):
    """dispatched 待处理→resolved；未派发待处理→invalid；终态不动（AC-003/008）。"""
    _seed_five_book(tmp_path)
    dispatched = create_annotation(tmp_path, _req("总纲.md", "第二段独立文字"))
    idle = create_annotation(
        tmp_path, _req("卷纲/第01卷.md", "关键设定", label=LABEL_CONTRADICTION)
    )
    anns = load_annotations(tmp_path)
    anns[0].dispatched = True
    save_annotations(tmp_path, anns)
    # 改写两处原文（保留彼此互不影响）
    (tmp_path / "总纲.md").write_text(
        "# 总纲\n> 状态：定稿\n\n全新内容，原文没了。\n\n回看当天：他一挥手，法则之力倾泻而下，把追兵尽数轰飞。\n",
        encoding="utf-8",
    )
    (tmp_path / "卷纲" / "第01卷.md").write_text("# 卷纲\n\n别的设定句。\n", encoding="utf-8")
    changed = reverify_annotations(tmp_path)
    assert changed == 2
    anns = {a.id: a for a in load_annotations(tmp_path)}
    assert anns[dispatched.id].status == "resolved"
    assert anns[idle.id].status == "invalid"


def test_reverify_keeps_pending_when_quote_intact(tmp_path: Path):
    """原文未变（agent 修改失败）时保持待处理（AC-008）。"""
    _seed_five_book(tmp_path)
    create_annotation(tmp_path, _req("总纲.md", "第二段独立文字"))
    anns = load_annotations(tmp_path)
    anns[0].dispatched = True
    save_annotations(tmp_path, anns)
    assert reverify_annotations(tmp_path) == 0
    assert load_annotations(tmp_path)[0].status == "pending"


def test_reverify_missing_file_marks_invalid(tmp_path: Path):
    _seed_five_book(tmp_path)
    create_annotation(tmp_path, _req("文风.md", "短句快节奏"))
    (tmp_path / "文风.md").unlink()
    reverify_annotations(tmp_path)
    assert load_annotations(tmp_path)[0].status == "invalid"


def test_dispatch_composes_task_and_marks(tmp_path: Path):
    """任务消息含标签默认指令＋原文＋意见；发出前复验剔除失效（AC-005/006/007）。"""
    _seed_five_book(tmp_path)
    a1 = create_annotation(tmp_path, _req("总纲.md", "第二段独立文字", note="看不懂"))
    a2 = create_annotation(
        tmp_path, _req("卷纲/第01卷.md", "法则之力", label=LABEL_CONTRADICTION)
    )
    # 再造一条锚点已失效的：dispatch 应剔除并转 invalid
    gone = create_annotation(
        tmp_path, _req("总纲.md", "核心纲", label=LABEL_CUSTOM, custom_label="自定义标签"
        )
    )
    (tmp_path / "总纲.md").write_text(
        "# 总纲\n> 状态：定稿\n\n他一挥手，法则之力倾泻而下，把追兵尽数轰飞。\n\n"
        "第二段独立文字。\n\n回看当天：他一挥手，法则之力倾泻而下，把追兵尽数轰飞。\n",
        encoding="utf-8",
    )
    message, ids, invalidated = dispatch_annotations(tmp_path)
    assert invalidated == 1
    assert set(ids) == {a1.id, a2.id}
    assert "【标注修订任务】" in message and "共 2 条" in message
    assert "抽象设定" in message and "看不懂" in message
    assert "前后矛盾" in message and "法则之力" in message
    assert "自定义标签" not in message  # 失效条目不进包
    anns = {a.id: a for a in load_annotations(tmp_path)}
    assert anns[a1.id].dispatched and anns[a1.id].status == "pending"
    assert anns[gone.id].status == "invalid"
    # 派发后原文被改 → dispatched 且原文消失 → resolved
    (tmp_path / "总纲.md").write_text("# 总纲\n> 状态：定稿\n\n全变。\n", encoding="utf-8")
    reverify_annotations(tmp_path)
    assert {a.id: a for a in load_annotations(tmp_path)}[a1.id].status == "resolved"


def test_dispatch_without_pending_raises(tmp_path: Path):
    _seed_five_book(tmp_path)
    with pytest.raises(ValueError, match="没有"):
        dispatch_annotations(tmp_path)


def test_clear_annotations_by_status(tmp_path: Path):
    """一键清空只删指定状态，待处理保留（FR-002）。"""
    _seed_five_book(tmp_path)
    pending = create_annotation(tmp_path, _req("总纲.md", "第二段独立文字"))
    resolved = create_annotation(tmp_path, _req("文风.md", "短句快节奏"))
    anns = load_annotations(tmp_path)
    anns[1].status = "resolved"
    save_annotations(tmp_path, anns)
    assert clear_annotations(tmp_path, ["resolved"]) == 1
    assert [a.id for a in load_annotations(tmp_path)] == [pending.id]
    assert resolved.id != pending.id


# ── 端点 ──────────────────────────────────────────────────


def test_endpoints_crud_roundtrip(client: TestClient, tmp_path: Path):
    _seed_five_book(tmp_path)
    resp = client.post(
        "/workspaces/ws1/annotations",
        json={"artifact_rel": "总纲.md", "label": "abstract", "quote": "第二段独立文字", "note": "虚"},
    )
    assert resp.status_code == 200, resp.text
    ann = resp.json()
    assert ann["status"] == "pending"

    listed = client.get("/workspaces/ws1/annotations").json()["annotations"]
    assert [a["id"] for a in listed] == [ann["id"]]

    updated = client.put(
        f"/workspaces/ws1/annotations/{ann['id']}",
        json={"note": "更虚了"},
    ).json()
    assert updated["note"] == "更虚了"

    # 重框选：换锚点并复位待处理
    reanchored = client.put(
        f"/workspaces/ws1/annotations/{ann['id']}",
        json={"reanchor": {"quote": "核心纲", "context_before": "", "context_after": ""}},
    ).json()
    assert reanchored["quote"] == "核心纲" and reanchored["status"] == "pending"

    assert client.delete(f"/workspaces/ws1/annotations/{ann['id']}").status_code == 200
    assert client.get("/workspaces/ws1/annotations").json()["annotations"] == []


def test_endpoint_rejects_ambiguous_and_out_of_scope(client: TestClient, tmp_path: Path):
    _seed_five_book(tmp_path)
    dup = "他一挥手，法则之力倾泻而下，把追兵尽数轰飞。"
    resp = client.post(
        "/workspaces/ws1/annotations",
        json={"artifact_rel": "总纲.md", "label": "abstract", "quote": dup},
    )
    assert resp.status_code == 422
    assert "出现多次" in resp.json()["detail"]
    # 主题.md 不在标注范围（DEC-006）
    resp = client.post(
        "/workspaces/ws1/annotations",
        json={"artifact_rel": "主题.md", "label": "abstract", "quote": "任意"},
    )
    assert resp.status_code == 422
    assert "主题" in resp.json()["detail"]


def test_endpoint_clear_and_dispatch(client: TestClient, tmp_path: Path):
    _seed_five_book(tmp_path)
    a1 = client.post(
        "/workspaces/ws1/annotations",
        json={"artifact_rel": "总纲.md", "label": "abstract", "quote": "第二段独立文字"},
    ).json()
    a2 = client.post(
        "/workspaces/ws1/annotations",
        json={"artifact_rel": "卷纲/第01卷.md", "label": "contradiction", "quote": "关键设定"},
    ).json()
    # 手工把 a2 置为终态，验证一键清空只删指定状态、待处理保留
    anns = load_annotations(tmp_path)
    next(a for a in anns if a.id == a2["id"]).status = "resolved"
    save_annotations(tmp_path, anns)
    cleared = client.post(
        "/workspaces/ws1/annotations/clear", json={"statuses": ["resolved"]}
    ).json()
    assert cleared["deleted"] == 1
    assert [a["id"] for a in client.get("/workspaces/ws1/annotations").json()["annotations"]] == [a1["id"]]

    dispatched = client.post("/workspaces/ws1/annotations/dispatch").json()
    assert dispatched["annotation_ids"] == [a1["id"]]
    assert "【标注修订任务】" in dispatched["message"]


def test_endpoint_dispatch_legacy_409(client: TestClient, tmp_path: Path):
    """legacy 工作区（旧 outline/ 目录结构）标注写操作 409（DEC-006 非目标）。"""
    (tmp_path / "outline").mkdir()
    resp = client.post(
        "/workspaces/ws1/annotations",
        json={"artifact_rel": "总纲.md", "label": "abstract", "quote": "x"},
    )
    assert resp.status_code == 409
    assert client.post("/workspaces/ws1/annotations/dispatch").status_code == 409
