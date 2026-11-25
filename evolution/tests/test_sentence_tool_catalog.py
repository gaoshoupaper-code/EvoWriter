"""版本要素视图给逐句写作的六个工具配上说明（REQ-20261125-154544，系统事实 13 的登记义务）。"""
from app.versioning import elements_api
from contracts.sentence_draft import HEADER_TOOL, SENTENCE_TOOLS


def test_element_catalog_describes_every_sentence_tool(monkeypatch):
    names = (*SENTENCE_TOOLS, HEADER_TOOL)
    monkeypatch.setattr(elements_api, "_list_files_at_commit",
                        lambda commit, path: [f"tools/{name}.py" for name in names])
    monkeypatch.setattr(elements_api, "show_file", lambda commit, path: '"""某个工具 — 模块说明的首行。"""\n')

    tools = {tool["name"]: tool for tool in elements_api._build_tool_infos("a" * 40)}

    for name in names:
        assert tools[name]["description"] == elements_api._TOOL_FUNCTIONS[name]
        assert tools[name]["load_error"] is None
    assert "整章一次写入" in tools[HEADER_TOOL]["description"]
