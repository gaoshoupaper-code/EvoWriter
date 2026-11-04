"""正文写作单元保存：完整 Skill 场景不被普通批长或工具回执拆开。"""
from __future__ import annotations

import os
from pathlib import Path

from app.platform.agent.loader import load_package

_HARNESS_DIR = Path(os.environ.get(
    "WRITER_SCENE_HARNESS_PATH",
    str(Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"),
))
load_package(_HARNESS_DIR)

from harness_current.tools import save_artifact as save_module  # noqa: E402
from harness_current.tools.read_artifact import build_read_artifact_tool  # noqa: E402


HEADER = "# 第001章 · 交换\n> 状态：起草中｜2026-11-04\n> 进度：已落 1 批\n\n"
PREFIX = HEADER + "林舟把账本放在桌上，掌柜还没有看见封皮下的欠条。\n"
SCENE = "\n\n".join([
    "林舟报出三十两。掌柜翻开账本，指着上面的旧印，表示最多出十两。" * 9,
    "林舟没有再降价。他提出附带运送一趟货物，运费抵扣五两，货损由掌柜承担。" * 9,
    "掌柜拿来契纸，把货损责任写入条款。林舟检查日期后落笔，收到了二十五两。" * 9,
    "林舟收好银子，手里少了一本账，却多了后天必须到码头履约的责任。" * 9,
])


def _save(root: Path, registry: dict | None = None):
    return save_module.build_save_artifact_tool(root, registry if registry is not None else {}, {})


def test_complete_scene_is_appended_as_one_piece_without_touching_prefix(tmp_path):
    """AC-003/005：大于普通批长的片段完整保存，接续正文也不覆盖前文。"""
    save = _save(tmp_path)
    assert "已落盘" in save.invoke({"kind": "chapter_text", "chapter": 1, "mode": "full", "content": PREFIX})
    assert len(SCENE) > 1000
    result = save.invoke({"kind": "chapter_text", "chapter": 1, "mode": "append", "content": SCENE})
    path = tmp_path / "正文" / "第001章.md"
    expected = PREFIX.rstrip("\n") + "\n\n" + SCENE + "\n"
    assert "已追加落盘" in result
    assert path.read_text(encoding="utf-8") == expected
    assert SCENE in path.read_text(encoding="utf-8")
    assert "已追加落盘" in save.invoke({
        "kind": "chapter_text", "chapter": 1, "mode": "append", "content": "林舟离开商铺。",
    })
    assert path.read_text(encoding="utf-8").startswith(expected)


def test_append_receipt_does_not_reimpose_the_ordinary_batch_limit(tmp_path):
    """AC-003/004：工具回执不能把刚写完的场景重新引导为固定批长。"""
    save = _save(tmp_path)
    save.invoke({"kind": "chapter_text", "chapter": 1, "mode": "full", "content": PREFIX})
    result = save.invoke({"kind": "chapter_text", "chapter": 1, "mode": "append", "content": SCENE})
    assert "已追加落盘" in result
    assert "每批 300-400 字" not in result
    assert "保存次数" in result
    assert "预算" in result


def test_failed_scene_append_leaves_the_saved_chapter_unchanged(tmp_path, monkeypatch):
    """AC-005：保存失败不能把完整前文变成半个场景。"""
    save = _save(tmp_path)
    save.invoke({"kind": "chapter_text", "chapter": 1, "mode": "full", "content": PREFIX})
    path = tmp_path / "正文" / "第001章.md"
    original = path.read_bytes()

    def fail_write(*args):
        raise OSError("scene acceptance: injected write failure")

    monkeypatch.setattr(save_module, "_atomic_write", fail_write)
    result = save.invoke({"kind": "chapter_text", "chapter": 1, "mode": "append", "content": SCENE})
    assert "写入失败" in result
    assert path.read_bytes() == original


def test_restarted_tools_read_the_saved_scene_before_continuing(tmp_path):
    """AC-005：新运行先读取实际保存内容，再接续场景之后的正文。"""
    save = _save(tmp_path)
    save.invoke({"kind": "chapter_text", "chapter": 1, "mode": "full", "content": PREFIX})
    save.invoke({"kind": "chapter_text", "chapter": 1, "mode": "append", "content": SCENE})
    registry: dict = {}
    restarted = _save(tmp_path, registry)
    args = {"kind": "chapter_text", "chapter": 1, "mode": "append", "content": "后天的货船已经靠岸。"}
    assert "先调 read_artifact" in restarted.invoke(args)
    readback = build_read_artifact_tool(tmp_path, registry).invoke({"kind": "chapter_text", "chapter": 1})
    assert "二十五两" in readback
    assert "已追加落盘" in restarted.invoke(args)
    text = (tmp_path / "正文" / "第001章.md").read_text(encoding="utf-8")
    assert text.count(SCENE) == 1
    assert text.index(SCENE) < text.index("后天的货船已经靠岸。")
