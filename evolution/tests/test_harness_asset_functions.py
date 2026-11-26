from unittest.mock import patch

from app.versioning import elements_api
from app.versioning.middleware_projection import build_middleware_projection, project_manifest_stacks
from contracts.architecture_manifest import ArchitectureManifest


def test_unregistered_writing_tools_get_function_summaries():
    sources = {
        "tools/save_artifact.py": '"""内部说明（REQ-000）。"""',
        "tools/update_character_state.py": 'def build(abc): ...',
        "tools/custom.py": '"""生成章节人物清单。"""',
    }
    with patch.object(elements_api, "_list_files_at_commit", return_value=list(sources)), \
         patch.object(elements_api, "show_file", side_effect=lambda _commit, path: sources[path]):
        by_name = {row["name"]: row for row in elements_api._build_tool_infos("sha")}

    assert "保存或修改" in by_name["save_artifact"]["description"]
    assert "人物现状" in by_name["update_character_state"]["description"]
    assert by_name["custom"]["description"] == "生成章节人物清单。"
    assert by_name["save_artifact"]["scope"] == {"kind": "unknown"}
    # REQ-20261125-210621 FR-003（AC-005）：源码里读不到说明时回退到现有文字，并标明。
    assert {by_name[name]["description_source"] for name in by_name} == {"fallback"}


def _tool_rows(sources):
    with patch.object(elements_api, "_list_files_at_commit", return_value=list(sources)), \
         patch.object(elements_api, "show_file", side_effect=lambda _commit, path: sources[path]):
        return {row["name"]: row for row in elements_api._build_tool_infos("sha")}


def test_tool_description_is_read_from_source_before_any_summary():
    """REQ-20261125-210621 FR-003（AC-004）：优先给出源码里写明的说明全文，换行原样保留。"""
    rows = _tool_rows({
        "tools/read_artifact.py": 'READ_ARTIFACT_TOOL_DESCRIPTION = """读产物第一行。\n读产物第二行。"""\n',
        "tools/update_character_state.py": (
            'def build(abc):\n    return StructuredTool.from_function(func=update, description="保留原有设定。")\n'),
        "tools/write_info.py": "def build(abc):\n    return build_paragraph_tool(abc, 'write_info', guidance, guide)\n",
        "tools/sentence_common.py": 'class ParagraphInput(BaseModel):\n    text: str = Field(description="这一段的成段正文")\n',
    })

    assert rows["read_artifact"]["description"] == "读产物第一行。\n读产物第二行。"
    assert rows["update_character_state"]["description"] == "保留原有设定。"
    assert rows["read_artifact"]["description_source"] == rows["update_character_state"]["description_source"] == "source"
    # 说明由模板拼出、或文件里只有参数说明：读不出工具说明，回退并标明。
    assert rows["write_info"]["description"] == elements_api._TOOL_FUNCTIONS["write_info"]
    assert rows["write_info"]["description_source"] == "fallback"
    assert rows["sentence_common"]["description"] is None
    assert rows["sentence_common"]["description_source"] == "fallback"


def test_save_artifact_shows_the_description_its_package_uses(monkeypatch):
    """REQ-20261125-210621 FR-003（AC-004）：save_artifact 有两份说明，按该版本是否逐句包取对应的一份。"""
    sources = {
        "tools/save_artifact.py": ('SAVE_ARTIFACT_TOOL_DESCRIPTION = """按产物种类落盘。"""\n'
                                   'SENTENCE_PACKAGE_TOOL_DESCRIPTION = """保存或修改本章正文。"""\n'),
        "tools/write_info.py": 'WRITE_INFO_TOOL_DESCRIPTION = """写一段信息。"""\n',
    }
    monkeypatch.setattr(elements_api, "_list_files_at_commit", lambda commit, path: list(sources))
    monkeypatch.setattr(elements_api, "show_file", lambda commit, path: sources.get(path, ""))
    monkeypatch.setattr(elements_api, "_build_skill_infos", lambda commit: [])
    monkeypatch.setattr(elements_api, "_manifest_middleware_stacks", lambda commit, manifest: {})

    def save_description(*tools):
        manifest = ArchitectureManifest.model_validate({
            "schema_version": "writer.architecture/1",
            "agents": [{"name": "text", "role": "main", "prompt": "prompts/main.md", "tools": list(tools)}],
        })
        rows = {row["name"]: row for row in elements_api._build_manifest_elements_view(0, "c" * 40, manifest)["tools"]}
        return rows["save_artifact"]["description"]

    assert save_description("save_artifact", "write_info") == "保存或修改本章正文。"
    assert save_description("save_artifact") == "按产物种类落盘。"


def test_revision_hooks_get_different_function_details():
    sources = {
        "__init__.py": "def middleware_factory():\n    mw = [RevisionLimitMiddleware()]\n",
        "middleware/revision_limit.py": (
            "class RevisionLimitMiddleware:\n"
            "    def before_agent(self, state, runtime): ...\n"
            "    def wrap_tool_call(self, request, handler): ...\n"
        ),
    }
    revision, = build_middleware_projection(sources)["storybuilding"]

    assert revision["hooks"] == ["before_agent", "wrap_tool_call"]
    hook_details = revision["hook_details"]
    assert "重置" in hook_details["before_agent"]["action"]
    assert "拦截" in hook_details["wrap_tool_call"]["action"]
    assert all(set(details) == {"trigger", "action", "effect"} for details in hook_details.values())


def test_static_projection_keeps_synchronous_after_hooks():
    sources = {
        "__init__.py": "def middleware_factory():\n    mw = [ExampleMiddleware()]\n",
        "middleware/example.py": (
            "class ExampleMiddleware:\n"
            "    def after_model(self, state, runtime):\n"
            '        """检查模型输出。"""\n'
            "    def after_agent(self, state, runtime):\n"
            '        """汇总本次产物。"""\n'
        ),
    }
    example, = build_middleware_projection(sources)["storybuilding"]

    assert example["hooks"] == ["after_model", "after_agent"]
    assert example["hook_details"]["after_model"]["action"] == "检查模型输出。"
    assert example["hook_details"]["after_agent"]["action"] == "汇总本次产物。"


def test_scoped_skill_view_only_lists_mounted_skill_entries(monkeypatch):
    manifest = ArchitectureManifest.model_validate({
        "schema_version": "writer.architecture/1",
        "agents": [{"name": "text", "role": "main", "prompt": "prompts/creative_text.md", "skills": ["draft"]}],
    })
    skills = [{"path": "skills/draft"}, {"path": "skills/draft/nested"}]
    monkeypatch.setattr(elements_api, "_build_skill_infos", lambda _commit: skills)
    monkeypatch.setenv("WRITING_AGENT_ID", "text")

    scoped = elements_api._build_manifest_elements_view(0, None, manifest)
    assert scoped["agents"][0]["skills"] == [skills[0]]

    monkeypatch.delenv("WRITING_AGENT_ID")
    legacy = elements_api._build_manifest_elements_view(0, None, manifest)
    assert legacy["agents"][0]["skills"] == skills


def test_manifest_resolves_the_factory_class_and_local_inherited_hooks():
    manifest = ArchitectureManifest.model_validate({
        "schema_version": "writer.architecture/1",
        "agents": [{"name": "text", "role": "main", "prompt": "prompts/main.md", "middleware": ["choice"]}],
    })
    declarations = (
        "class ParentMiddleware:\n"
        "    def before_model(self, state, runtime): ...\n"
        "class ChosenMiddleware(ParentMiddleware):\n"
        "    async def aafter_agent(self, state, runtime): ...\n"
        "class OtherMiddleware:\n"
        "    def wrap_tool_call(self, request, handler): ...\n"
    )
    path = "middleware/choice.py"
    for factory, name, hooks in [
        ("    return ChosenMiddleware()\n", "ChosenMiddleware", ["before_model", "after_agent"]),
        ("    if abc:\n        return ChosenMiddleware()\n    return OtherMiddleware()\n", "choice", []),
    ]:
        stack = project_manifest_stacks({path: declarations + "def build(abc):\n" + factory}, manifest)["text"]
        chosen, = [row for row in stack if row["source_path"] == path]
        assert chosen["class_name"] == name
        assert chosen["hooks"] == hooks


def test_historical_empty_stack_requires_projection_evidence(monkeypatch):
    sources = {
        "__init__.py": "def middleware_factory():\n    mw = []\n",
        "subagents/storybuilding.py": (
            "def build_storybuilding_deep_subagent():\n"
            "    storybuilding_middleware = []\n"
        ),
    }
    monkeypatch.setattr(elements_api, "_list_files_at_commit", lambda _commit, _directory: [])
    monkeypatch.setattr(elements_api, "show_file", lambda _commit, path: sources[path])

    view = elements_api._elements_at(7, "known-commit")
    main, helper = view["agents"]
    assert view["has_source"]
    assert main["middlewares"] == helper["middlewares"] == []
    assert main["middleware_available"] is True
    assert helper["middleware_available"] is False
