"""Four isolated experiments must never depend on another writer's samples."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.mark.parametrize(("agent", "kind"), [
    ("outline", "master_outline"), ("volume", "volume_outlines"),
    ("chapter", "chapter_outlines"), ("text", "chapter_text"),
])
def test_four_fresh_packages_with_only_local_dependencies(monkeypatch, agent, kind):
    from app.experiments import packages, seed_data
    from app.experiments.schemas import PackageCreate

    monkeypatch.setenv("WRITING_AGENT_ID", agent)
    cases = seed_data.get_builtin_packages()
    assert len(cases) == 4
    assert {case["task_type"] for case in cases} == {kind}
    assert sorted(case["purpose"] for case in cases) == ["optimization"] * 2 + ["regression"] * 2
    for case in cases:
        assert case["suite"] == "isolated-writing-v1"
        payload = {key: case[key] for key in ("title", "purpose", "target", "input")}
        packages.validate_package(PackageCreate.model_validate(payload))
        assert "主题.md" not in case["input"]["files"]
        if kind == "chapter_text":
            assert list(case["input"]["files"]) == [f"章纲/第{case['target']['chapter']:03d}章.md"]
            assert not case["target"]["volume_ids"] and not case["input"]["demand_md"]


def test_chapter_case_preserves_imported_skills_only_in_new_runs():
    from app.experiments import repo
    from contracts.target_tasks import TaskInput

    skills = [{"stage": "text", "name": "对白", "content": "对白简短。"}]
    inputs = TaskInput(demand_md="旧需求", confirmation_rules="直接写。", skills=skills,
                       files={"章纲/第002章.md": "章纲", "卷纲/第01卷.md": "卷纲", "文风.md": "文风"})
    package = {"target": {"chapter": 2}, "input": inputs.model_dump(), "input_revision": inputs.input_revision}
    current = repo.chapter_case(package)
    assert current["input"]["skills"] == skills
    assert current["input"]["files"] == {"章纲/第002章.md": "章纲"}
    assert not current["input"]["demand_md"]
    assert current["input_revision"] != inputs.input_revision
    historical = repo.chapter_case(package, 1)
    assert historical["input"]["skills"] == []
    assert historical["input"]["files"] == inputs.files
    assert package["input"]["skills"] == skills


def test_five_point_gain_requires_complete_paired_runs(monkeypatch):
    from app.experiments.evaluation import compare_results

    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    def sample(key, score):
        return {"package_id": key, "completed": score is not None,
                "scores": {"overall": score} if score is not None else None}
    old = [sample("a", 4), sample("b", 4)]
    new = [sample("a", 3.8), sample("b", 4.2)]
    assert compare_results(old, new, completion_rate=1, quality_gain=0)["passed"]
    old[0] = sample("a", None)
    assert not compare_results(old, new, completion_rate=1, quality_gain=0)["passed"]


def test_rejected_inputs_and_grade_rules(monkeypatch):
    from app.experiments.schemas import RunCreate
    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    kind = "chapter_text"
    options = dict(task_type=kind, rule_versions={kind: "text-rules"}, optimization_ids=["a", "b"],
                   regression_ids={kind: ["c", "d"]}, model_config_id=1, judge_group_id=1, baseline_version=1)
    for invalid in ({"task_type": "chapter_outlines"}, {"validation_ids": ["x"]}, {"max_rounds": 4},
                    {"regression_ids": {kind: ["a"]}}, {"regression_ids": {"volume_outlines": ["x"]}},
                    {"case_selection": "custom", "optimization_ids": []},
                    {"case_selection": "custom", "regression_ids": {kind: []}},
                    {"thresholds": {"quality_gain": 0}}, {"thresholds": {"completion_rate": .5}}):
        with pytest.raises(ValueError):
            RunCreate.model_validate({**options, **invalid})


def test_http_suite_boundaries_and_frozen_rubric(monkeypatch):
    import sqlite3
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from app.core import db
    from app.experiments import api, repo

    monkeypatch.setenv("WRITING_AGENT_ID", "outline")
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    monkeypatch.setattr(db, "_conn", connection)
    db.init_db()
    app = FastAPI()
    @app.middleware("http")
    async def as_admin(request: Request, call_next):
        request.state.is_super_admin = True
        return await call_next(request)
    app.include_router(api.router)
    try:
        with TestClient(app) as client:
            listed = client.get("/experiments/packages").json()["items"]
            assert len(listed) == 4
            valid = client.get("/experiments/packages/" + listed[0]["id"]).json()
            for hidden_id, hidden in [("old", {"flow_version": 2}), ("foreign", {"task_type": "chapter_text"})]:
                repo.insert("self_evolution_packages", {**valid, "id": hidden_id, **hidden})
                assert client.get("/experiments/packages/" + hidden_id).status_code == 404
                assert client.delete("/experiments/packages/" + hidden_id).status_code == 404
            assert client.delete("/experiments/packages/missing").status_code == 404
            assert len(client.get("/experiments/packages/templates").json()["items"]) == 1
            assert client.get("/experiments/rules?task_type=chapter_text").status_code == 404
            rubric = client.get("/experiments/rules").json()["items"][0]
            assert [d["key"] for d in rubric["dimensions"]] == ["主题清晰度", "主角目标与冲突", "主线转折因果", "可分卷性"]
            assert {d["weight"] for d in rubric["dimensions"]} == {1}
            payload = {key: rubric[key] for key in ("task_type", "profile", "name", "dimensions", "discipline_rules")}
            payload["dimensions"][0]["weight"] = 2
            assert client.post("/experiments/rules", json=payload).status_code == 422
            payload["task_type"] = "chapter_text"
            assert client.post("/experiments/rules", json=payload).status_code == 422
            repaired = {key: valid[key] for key in ("title", "purpose", "target", "input")}
            repaired["input"].pop("input_revision")
            repaired["input"]["demand_md"] += " 说明主角失败的代价。"
            updated = client.put("/experiments/packages/" + valid["id"], json=repaired)
            assert updated.status_code == 200
            assert updated.json()["id"] != valid["id"]
            visible = client.get("/experiments/packages").json()["items"]
            assert len(visible) == 4 and valid["id"] not in {p["id"] for p in visible}
            assert client.get("/experiments/packages/" + valid["id"]).json()["input_revision"] == valid["input_revision"]
            assert client.put("/experiments/packages/foreign", json=repaired).status_code == 404
            assert client.delete("/experiments/packages/" + valid["id"]).status_code == 404
            updated_url = "/experiments/packages/" + updated.json()["id"]
            assert client.delete(updated_url).json() == {"ok": True}
            deleted = client.get(updated_url).json()
            assert deleted["input_revision"] == updated.json()["input_revision"]
            assert deleted["input"]["demand_md"] == repaired["input"]["demand_md"]
            assert client.delete(updated_url).json() == {"ok": True}
            assert client.get(updated_url).json()["deleted_at"] == deleted["deleted_at"]
            assert client.put(updated_url, json=repaired).status_code == 404
            assert {p["id"] for p in client.get("/experiments/packages").json()["items"]} == {
                p["id"] for p in listed if p["id"] != valid["id"]
            }
            monkeypatch.delenv("WRITING_AGENT_ID")
            assert client.get("/experiments/packages").status_code == 404
    finally:
        connection.close()


@pytest.mark.parametrize("pass_round", [2, None])
@pytest.mark.parametrize("case_selection", ["all", "custom"])
def test_new_run_retries_only_local_cases_then_requires_approval(monkeypatch, tmp_path, pass_round, case_selection):
    import sqlite3
    from app.core import db
    from app.core.security import encrypt_secret
    from app.experiments import agent, api, clients, evaluation, release as publishing, repo, rubrics, runner
    from app.experiments.schemas import RuleCreate, RunCreate

    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    monkeypatch.setattr(db.settings, "evolution_db", str(tmp_path / "evolution.db"))
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    monkeypatch.setattr(db, "_conn", connection)
    monkeypatch.setattr(db, "_master_key_cache", bytes(range(32)))
    db.init_db()
    repo.init_schema()
    try:
        for i, scope, model in ((1, "executor", "gpt-test"), (2, "eval", "claude-test"), (3, "evolution", "gpt-test")):
            db.execute("INSERT INTO llm_configs(id,name,api_key_enc,base_url,model,is_active,scope,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                       (i, model, encrypt_secret("test-key", db.get_master_key()), "https://test.invalid", model, 1, scope, "now", "now"))
        db.execute("INSERT INTO judge_groups(id,name,created_at,updated_at) VALUES (1,'评委','now','now')")
        db.execute("INSERT INTO judge_group_members(group_id,config_id,position) VALUES (1,2,0)")
        kind = "chapter_text"
        cases = repo.list_packages()
        opt = [case["id"] for case in cases if case["purpose"] == "optimization"]
        reg = [case["id"] for case in cases if case["purpose"] == "regression"]
        settings = RunCreate.model_validate(dict(task_type=kind, rule_versions={kind: rubrics.list_rules(kind)[0]["id"]},
            optimization_ids=opt, regression_ids={kind: reg}, model_config_id=1, judge_group_id=1, baseline_version=2))
        release = {"commit": "a" * 40, "version": 2}
        monkeypatch.setattr(clients, "production", lambda: release)
        previous = {"commit": "f" * 40, "version": 1}
        monkeypatch.setattr(clients, "versions", lambda: {"items": [release, previous], "production_version": 2})
        monkeypatch.setattr(clients, "prepare_artifact", lambda _: {"digest": "1" * 64})
        current_rule = rubrics.get_rule(settings.rule_versions[kind])
        repo.insert("self_evolution_rules", {**current_rule, "id": "old-four-dimensions", "version": 99,
            "dimensions": current_rule["dimensions"][:4]})
        assert api.experiment_options()["versions"] == [release]
        assert api.experiment_options()["launch_rule_ids"] == [current_rule["id"]]
        with pytest.raises(ValueError, match="全部现行正文维度"):
            runner.create_run(settings.model_copy(update={"rule_versions": {kind: "old-four-dimensions"}}), "author")
        assert previous in clients.versions()["items"]
        with pytest.raises(ValueError, match="当前生产版本"):
            runner.create_run(settings.model_copy(update={"baseline_version": previous["version"]}), "author")
        with pytest.raises(ValueError, match="全部在用"):
            runner.create_run(settings.model_copy(update={"optimization_ids": opt[:1]}), "author")
        all_settings = settings
        if case_selection == "custom":
            settings = RunCreate.model_validate({**settings.model_dump(), "case_selection": case_selection,
                "optimization_ids": opt[:1], "regression_ids": {kind: reg[:1]}})
            with pytest.raises(ValueError, match="刷新题库"):
                runner.create_run(settings.model_copy(update={"optimization_ids": reg[1:]}), "author")
        with pytest.raises(ValueError, match="自动"):
            runner.create_run(settings.model_copy(update={"candidate_commit": "b" * 40}), "author")
        from app.experiments import snapshots
        monkeypatch.setattr(snapshots, "ensure_baseline", lambda commit: None)
        monkeypatch.setattr(snapshots, "retain", lambda archive, commit: None)
        run = runner.create_run(settings, "author")
        selected_opt, selected_reg = settings.optimization_ids, settings.regression_ids[kind]
        coverage = {"optimization": {"selected": len(selected_opt), "total": len(opt)},
                    "regression": {"selected": len(selected_reg), "total": len(reg)}}
        assert run["case_coverage"] == coverage
        created_ids = {run["id"]}
        if case_selection == "custom":
            custom_all = RunCreate.model_validate({**all_settings.model_dump(), "case_selection": "custom"})
            custom_all_run = runner.create_run(custom_all, "author")
            created_ids.add(custom_all_run["id"])
            assert custom_all_run["case_coverage"] == {purpose: {"selected": group["total"], "total": group["total"]}
                                                      for purpose, group in coverage.items()}
        assert run["assessment_version"] == 2
        assert run["runtime_scoring_version"] == 1
        assert run["judge_input_version"] == 2
        assert run["judge_repeats"] == 3
        assert run["regression_repeats"] == 1
        first_rule = run["private"]["rules"][kind]
        next_rule = rubrics.save_rule(RuleCreate.model_validate({key: first_rule[key] for key in
            ("task_type", "profile", "name", "dimensions", "discipline_rules")}))
        db.JudgeGroupsRepository.update(1, name="改过的评委组", member_config_ids=[3], score_repeats=1)
        db.execute("UPDATE llm_configs SET model = 'gemini-test' WHERE id = 3")
        updated = runner.create_run(settings.model_copy(update={"rule_versions": {kind: next_rule["id"]}}), "author")
        created_ids.add(updated["id"])
        assert updated["judge_repeats"] == 1
        assert updated["models"]["judges"][0]["id"] == 3
        assert updated["private"]["rules"][kind]["id"] == next_rule["id"]
        repo.insert("self_evolution_packages", {**repo.get_package(opt[1]), "id": "added-question"})
        with pytest.raises(ValueError, match="刷新题库"):
            runner.create_run(all_settings, "author")
        changed = repo.get_package(opt[0])
        changed["id"] = "new-question"
        changed["input"]["demand_md"] = "实验启动后维护的内容"
        repo.replace_package(opt[0], changed)
        if case_selection == "custom":
            with pytest.raises(ValueError, match="刷新题库"):
                runner.create_run(settings, "author")
        removed = repo.get_package(reg[0])
        repo.delete_package(reg[0])
        if case_selection == "custom":
            replacement_settings = settings.model_copy(update={"optimization_ids": opt[1:]})
            with pytest.raises(ValueError, match="刷新题库"):
                runner.create_run(replacement_settings, "author")
        db.JudgeGroupsRepository.delete(1)
        repo.init_schema()
        assert reg[0] not in {sample["id"] for sample in repo.list_packages()}
        saved = repo.get_run(run["id"])
        assert saved["case_coverage"] == coverage
        for summary in (True, False):
            assert api.public_run(saved, can_read_content=False, summary=summary)["case_coverage"] == coverage
            old = {key: value for key, value in saved.items() if key != "case_coverage"}
            assert "case_coverage" not in api.public_run(old, can_read_content=False, summary=summary)
        assert "packages" not in saved["private"]
        assert saved["runtime_scoring_version"] == 1
        assert saved["judge_repeats"] == 3
        assert saved["regression_repeats"] == 1
        assert saved["models"]["judges"][0]["id"] == 2
        assert saved["private"]["rules"][kind] == first_rule
        assert repo.run_package(saved, reg[0])["input"] == repo.chapter_case(removed)["input"]
        frozen_input = repo.run_package(saved, opt[0])["input"]
        assert frozen_input["demand_md"] != changed["input"]["demand_md"]
        archived = {**run, "id": "old", "flow_version": 2}
        foreign = {**run, "id": "foreign", "task_type": "chapter_outlines"}
        for hidden in (archived, foreign):
            repo.insert("self_evolution_runs", hidden)
            assert repo.get_run(hidden["id"]) is None
        assert {row["id"] for row in repo.list_runs()} == created_ids
        def completed(payload):
            return {"task_id": payload["task_id"], "status": "completed", "completed": True, "trace_ids": [],
                    "artifacts": {"正文/第001章.md": "测试文稿"}, "checks": {"complete": True},
                    "input_revision": payload["input"]["input_revision"], "source_commit": payload["source_commit"],
                    "artifact_digest": payload["artifact_digest"]}
        def improve(_run, record, evidence):
            assert evidence == []
            assert record["base_commit"] == release["commit"]
            return {"commit": "bcd"[record["round"] - 1] * 40, "digest": "2" * 64,
                    "summary": "调整候选", "diff": "变更"}
        def score(_run, sample):
            value = 4.0
            if sample["arm"] == "candidate":
                if sample["phase"] == "regression":
                    value += (.2 if sample["package_id"] == reg[0] else -.2) if pass_round == sample["round"] else -.1
                else:
                    value += .1
            return {"overall": value, "dimensions": {}, "reasons": {}}
        monkeypatch.setattr(clients, "start_task", completed)
        monkeypatch.setattr(evaluation, "score_sample", score)
        monkeypatch.setattr(agent, "improve_candidate", improve)
        for _ in range(130):
            runner.advance_one(run["id"])
            end = repo.get_run(run["id"])
            if end["status"] not in runner.ACTIVE:
                break
        assert end["status"] == ("awaiting_review" if pass_round else "exhausted")
        assert end["current_round"] == (pass_round or 3)
        assert end["publish_status"] == "not_started"
        assert {sample["package_id"] for sample in end["samples"]} == set(selected_opt + selected_reg)
        assert "validation" not in {sample["phase"] for sample in end["samples"]}
        regression_samples = [sample for sample in end["samples"] if sample["phase"] == "regression"]
        assert len(regression_samples) == 2 * len(selected_reg) * end["current_round"]
        assert {(sample["round"], sample["arm"], sample["package_id"], sample["repeat"]) for sample in regression_samples} == {
            (round_number, arm, package_id, 1)
            for round_number in range(1, end["current_round"] + 1)
            for arm in ("baseline", "candidate")
            for package_id in selected_reg
        }
        assert all(record["regressions"][kind]["paired_count"] == len(selected_reg) for record in end["rounds"])
        assert api.public_run(end, can_read_content=False)["can_publish"] == bool(pass_round)
        if pass_round:
            candidate = end["best_candidate"]
            published = {"version": 3, "commit": candidate["commit"], "artifact_digest": candidate["digest"]}
            calls = []
            def promote(payload):
                calls.append(payload)
                return published
            monkeypatch.setattr(clients, "promote", promote)
            monkeypatch.setattr(clients, "runtime", lambda: published)
            historical = {**end, "id": "historical-baseline", "baseline": previous}
            repo.put_run(historical)
            with pytest.raises(ValueError, match="候选过期"):
                publishing.publish_run(historical["id"], "author")
            assert repo.get_run(historical["id"])["status"] == "stale" and not calls
            for _ in range(2):
                assert publishing.publish_run(end["id"], "author")["published"] == published
            assert len(calls) == 1
            assert calls[0]["expected_production_version"] == release["version"]
            assert calls[0]["expected_production_commit"] == release["commit"]
        else:
            with pytest.raises(ValueError, match="仅评测达标"):
                publishing.publish_run(end["id"], "author")
    finally:
        connection.close()
