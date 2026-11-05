"""Actual review evidence and frozen model identities, independent of model claims."""
from types import SimpleNamespace

from langchain_core.messages import ToolMessage

from contracts.target_tasks import TargetTaskRequest
from app.domains.writing.target_execution import FrozenExecution
from app.domains.writing.target_tasks import build_frozen_model


def _execution(tmp_path):
    request = TargetTaskRequest(
        task_id="text-evidence", attempt_id="first", source_commit="a" * 40,
        target={"task_type": "chapter_text", "chapter": 1},
        input={"confirmation_rules": "确认通过"},
    )
    (tmp_path / "正文").mkdir()
    (tmp_path / "正文/第001章.md").write_text("# 第001章\n> 状态：初稿\n原始正文", encoding="utf-8")
    return FrozenExecution(request, object(), tmp_path, object(), tmp_path)


def test_only_successful_actual_review_and_body_change_prove_revision(tmp_path):
    execution = _execution(tmp_path)
    execution.observe_event({"event": "on_tool_start", "name": "task", "run_id": "review-one",
                             "data": {"input": {"subagent_type": "review", "description": "审第1章"}}})
    execution.observe_event({"event": "on_tool_end", "name": "task", "run_id": "review-one",
                             "data": {"output": ToolMessage(content="位置：原始正文\n问题：因果断裂\n改法：补充原因", tool_call_id="one")}})
    assert execution.evidence["review"] == "success"
    assert execution.evidence["needs_revision"]
    path = tmp_path / "正文/第001章.md"
    path.write_text("# 第001章\n> 状态：初稿｜新日期\n原始正文", encoding="utf-8")
    execution.observe_event({"event": "on_tool_end", "name": "save_artifact", "run_id": "save-one", "data": {"output": "已保存"}})
    assert not execution.evidence["revised"]
    path.write_text("# 第001章\n> 状态：初稿\n补充原因后的真实正文", encoding="utf-8")
    execution.observe_event({"event": "on_tool_end", "name": "save_artifact", "run_id": "save-two", "data": {"output": "已保存"}})
    assert execution.evidence["revised"]
    assert "原始正文" in execution.evidence["initial_draft"]


def test_failed_review_cannot_use_a_success_claim(tmp_path):
    execution = _execution(tmp_path)
    execution.observe_event({"event": "on_tool_start", "name": "task", "run_id": "review-one",
                             "data": {"input": {"subagent_type": "review", "description": "审第1章"}}})
    execution.observe_event({"event": "on_tool_end", "name": "task", "run_id": "review-one",
                             "data": {"output": ToolMessage(content="通过，无修改意见", tool_call_id="one", status="error")}})
    assert execution.evidence["review"] == "failed"


def test_frozen_experiment_key_is_fetched_from_its_reference(monkeypatch):
    from app.domains.writing import models
    import httpx
    request = TargetTaskRequest(
        task_id="fixed-model", attempt_id="first", source_commit="a" * 40,
        target={"task_type": "volume_outlines", "volume_ids": [1]},
        input={"demand_md": "需求", "confirmation_rules": "确认"},
        model_snapshot={"model": "frozen-model", "base_url": "https://frozen.example/v1",
                        "source": "evolution", "api_key_ref": "experiment:exp-one:model"},
    )
    seen = []

    def get(url, **kwargs):
        seen.append((url, kwargs))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            "model": "frozen-model", "base_url": "https://frozen.example/v1", "api_key": "private-key",
        })

    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(models, "build_writer_model", lambda settings, **kwargs: kwargs)
    model = build_frozen_model(request, SimpleNamespace(evolution_url="http://evolution", evolution_notify_token="notify"))
    assert seen[0][0] == "http://evolution/api/ingestion/experiment-key/exp-one"
    assert seen[0][1]["headers"] == {"X-Notify-Token": "notify"}
    assert model["model_name_override"] == "frozen-model"
    assert model["api_key"] == "private-key"


def test_bound_package_is_used_after_production_changes(monkeypatch, tmp_path):
    from app.domains.writing.agent import resolve_run_package
    from app.platform.agent import artifact_client, loader
    bound_package = SimpleNamespace(__name__="bound-package")
    calls = []
    monkeypatch.setattr(artifact_client, "get_artifact_client", lambda: SimpleNamespace(
        ensure_artifact=lambda commit: calls.append(commit) or tmp_path,
    ))
    monkeypatch.setattr(loader, "load_package", lambda root, name: bound_package)
    monkeypatch.setattr(loader, "load_current_package", lambda: (_ for _ in ()).throw(AssertionError("生产已变化，不能装配当前包")))
    package, root, commit = resolve_run_package(SimpleNamespace(harness_commit="a" * 40))
    assert package is bound_package and root == tmp_path and commit == "a" * 40
    assert calls == ["a" * 40]
