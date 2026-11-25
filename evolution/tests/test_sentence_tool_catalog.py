"""版本要素视图展示逐句写作六个工具的说明全文（REQ-20261125-210621 FR-003，AC-004）。"""
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
    action = tools["write_action"]["description"]
    assert action.startswith("写一句「动作」：") and action.endswith("等工具返回后再写下一句。")
    assert action.count("\n") == 4
    assert "把本轮写好的句子整章写入文件" in tools[HEADER_TOOL]["description"]
