import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import db
from app.experiments import agent, clients, repo, runner, snapshots
from contracts.assessment_snapshots import git


@pytest.fixture
def dossier(tmp_path, monkeypatch):
    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    monkeypatch.setenv("ASSESSMENT_SNAPSHOT_DIR", str(tmp_path / "private"))
    monkeypatch.setattr(db.settings, "evolution_db", str(tmp_path / "data" / "evolution.db"))
    db.settings.db_path.parent.mkdir(exist_ok=True)
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()
    repo.init_schema()
    run = {"id": "experiment-example", "assessment_version": 1, "flow_version": 3, "task_type": "chapter_text",
           "status": "running", "created_at": repo.now(), "baseline": {"commit": "a" * 40},
           "events": [], "samples": [], "rounds": [], "private": {"package_revisions": {}}}
    yield run
    db.get_conn().close()


def test_immutable_case_reference_and_categorized_archive(dossier, tmp_path, monkeypatch):
    from contracts.target_tasks import TaskInput
    case = repo.list_packages()[0]
    source = TaskInput.model_validate({**case["input"], "input_revision": "",
        "skills": [{"name": "只随旧题出现", "stage": "text", "content": "旧技能正文"}]})
    case.update(id="with-legacy-skills", input=source.model_dump(mode="json"), input_revision=source.input_revision)
    repo.insert("self_evolution_packages", case)
    dossier["private"]["package_revisions"][case["id"]] = source.input_revision
    effective = repo.run_package(dossier, case["id"])
    assert effective["input"]["skills"] == [] and effective["input"]["files"] == case["input"]["files"]
    assert effective["input_revision"] != source.input_revision
    sample = {"id": "sample-1", "round": 0, "source_commit": "a" * 40, "artifacts": {"正文/第001章.md": "第一段\r\n第二段"},
              "trace_ids": ["writing-trace"], "scores": {"overall": 4.1}, "checks": {"complete": True}}
    record = {"round": 1, "base_commit": "a" * 40, "source_commit": "b" * 40, "status": "failed",
              "design": "根据原版审稿意见修改写作技能", "diff": "改动内容", "trace_ids": ["evolution-trace"],
              "startup_check": {"status": "failed", "error": "候选装配失败"}}
    dossier["samples"].append(sample)
    dossier["rounds"].append(record)
    repo.put_run(dossier)
    repo.put_run(dossier)
    stored = db.query_one("SELECT payload FROM self_evolution_runs WHERE id=?", (dossier["id"],))["payload"]
    assert all(text not in stored for text in ("旧技能正文", "第一段", "原版审稿意见", *source.files.values()))
    kinds = db.query_all("SELECT DISTINCT category FROM assessment_entries WHERE assessment_id=?", (dossier["id"],))
    assert {row["category"] for row in kinds} == {"version", "artifact", "evaluation", "improvement", "trace"}
    result = db.query_one("SELECT payload FROM assessment_entries WHERE assessment_id=? AND category='evaluation' AND entry_id='round-1'", (dossier["id"],))
    assert json.loads(result["payload"])["startup_check"] == record["startup_check"]
    db.get_conn().close()
    monkeypatch.setattr(db, "_conn", None)
    started = []
    monkeypatch.setattr(runner, "start_worker", started.append)
    runner.recover_runs()
    assert started == [dossier["id"]]
    loaded = repo.get_run(dossier["id"])
    assert loaded["status"] == "recovering"
    assert loaded["samples"] == [sample] and loaded["rounds"] == [record]
    assert len(list((tmp_path / "data/assessments/experiment-example/artifacts").iterdir())) == 1
    assert repo.run_package(loaded, case["id"]) == effective


def test_design_links_only_cited_optimization_samples(tmp_path, monkeypatch):
    from app.evolve import docs
    monkeypatch.setattr(docs, "session_dir", lambda _: tmp_path)
    path = docs.write_design_doc("test", changes=[{"target": "skills/scene/SKILL.md",
        "evidence_ref": ["source", "holdout", "other-run"]}], rationale="根据审稿意见补写线索")
    run = {"settings": {"optimization_ids": ["chapter"]}, "samples": [
        {"id": "source", "phase": "baseline", "package_id": "chapter"},
        {"id": "uncited", "phase": "exploration", "package_id": "chapter"},
        {"id": "holdout", "phase": "regression", "package_id": "other-chapter"}]}
    record = {}
    agent._record_design(run, record, path)
    assert record["evidence_samples"] == ["source"]
    assert "根据审稿意见补写线索" in record["design"]


@pytest.mark.parametrize("failure", ["writing", "validation", "startup"])
def test_failed_private_revisions_remain_recoverable(dossier, tmp_path, monkeypatch, failure):
    work = tmp_path / "origin"
    work.mkdir()
    git(work, "init", "--quiet")
    (work / "prompts").mkdir()
    (work / "prompts/text.md").write_text("original", encoding="utf-8")
    git(work, "add", ".")
    git(work, "-c", "user.name=Test", "-c", "user.email=test@local", "commit", "-m", "original")
    base = git(work, "rev-parse", "HEAD")
    bare = tmp_path / "central.git"
    git(tmp_path, "clone", "--bare", str(work), str(bare))
    monkeypatch.setattr(db.settings, "harness_bare_repo", str(bare))
    dossier["baseline"] = {"commit": base}
    snapshots.ensure_baseline(base)
    snapshots.retain(dossier["id"], base)
    record = {"round": 1, "base_commit": base, "status": "evolving"}
    dossier["rounds"].append(record)
    async def change(run, current, root, evidence):
        assert git(root, "remote") == "" and evidence == []
        (root / "prompts/text.md").write_text("candidate", encoding="utf-8")
        current["design"] = "优化场景描写"
        if failure == "writing":
            raise ValueError("改进未完成")
        if failure == "validation":
            (root / "tools").mkdir()
            (root / "tools/check.py").write_text("def check(\n", encoding="utf-8")
        return {"validated": failure != "validation", "summary": "修改提示词"}
    monkeypatch.setattr(agent, "_improve", change)
    def reject(commit):
        raise ValueError("助手未成功装配")
    monkeypatch.setattr(clients, "prepare_artifact", reject)
    with pytest.raises(ValueError, match="改进未完成|助手未成功装配|源码校验未通过"):
        agent.improve_candidate(dossier, record, [])
    saved = repo.get_run(dossier["id"])["rounds"][0]
    assert saved["source_commit"] != base and "candidate" in saved["diff"]
    assert base in git(bare, "show-ref") and saved["source_commit"] not in git(bare, "show-ref")
    snapshots.checkout_base(saved["source_commit"], tmp_path / "restored")
    assert (tmp_path / "restored/prompts/text.md").read_text() == "candidate"
    if failure == "startup":
        assert saved["startup_check"] == {"status": "failed", "error": "助手未成功装配"}
        assert saved["summary"] == "修改提示词"
    elif failure == "validation":
        assert saved["startup_check"]["status"] == "failed"
        assert "SyntaxError" in saved["startup_check"]["error"]
