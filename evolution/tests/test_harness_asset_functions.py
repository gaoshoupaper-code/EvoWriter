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
