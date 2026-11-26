"""版本要素视图展示逐段写作五个工具的说明全文。"""
from pathlib import Path

from app.versioning import elements_api
from contracts.sentence_draft import HEADER_TOOL, SENTENCE_TOOLS

_NAMES = (*SENTENCE_TOOLS, HEADER_TOOL)
_SEED = Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo" / "tools"


def test_element_catalog_shows_the_descriptions_the_model_reads(monkeypatch):
    sources = {f"tools/{name}.py": (_SEED / f"{name}.py").read_text(encoding="utf-8") for name in _NAMES}
    monkeypatch.setattr(elements_api, "_list_files_at_commit", lambda commit, path: list(sources))
    monkeypatch.setattr(elements_api, "show_file", lambda commit, path: sources[path])

    tools = {tool["name"]: tool for tool in elements_api._build_tool_infos("a" * 40)}

    for name in _NAMES:
        assert tools[name]["description_source"] == "source"
        assert tools[name]["load_error"] is None
    info = tools["write_info"]["description"]
    assert info.startswith("写一段用于确立客观坐标的场景信息") and info.endswith("改用 write_transition。")
    causal = tools["write_causal"]["description"]
    assert causal.startswith("写一段真正推动剧情的核心因果单元") and "群像不失声" in causal
    transition = tools["write_transition"]["description"]
    assert transition.startswith("写一段连接两个因果节点的过渡内容") and "快进压缩" in transition
    setup = tools["write_setup"]["description"]
    assert setup.startswith("写一段用于建立叙事前提的铺垫内容") and "伏笔" in setup
    assert "把本轮写好的段落整章写入文件" in tools[HEADER_TOOL]["description"]
