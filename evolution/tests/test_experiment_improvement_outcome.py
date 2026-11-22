"""Chapter improvement finishes when it cannot produce a qualifying candidate."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import db
from app.experiments import agent, api, clients, repo, runner
from test_experiments import run_record


@pytest.fixture
def chapter_improvement(monkeypatch, tmp_path):
    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    monkeypatch.setattr(db.settings, "evolution_db", str(tmp_path / "evolution.db"))
    monkeypatch.setattr(runner, "_stops", {})
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(db, "_conn", conn)
    db.init_db()
    repo.init_schema()
    run = run_record()
    run.update(task_type="chapter_text", assessment_version=2, flow_version=3, status="running",
               phase="evolving", current_round=0, best_candidate=None, report={})
    run["samples"] = [{"id": "original-1", "round": 0, "phase": "baseline", "package_id": "opt",
                       "source_commit": run["baseline"]["commit"], "status": "completed"}]
    repo.put_run(run)
    try:
        yield run["id"]
    finally:
        conn.close()


def test_no_change_keeps_baseline_and_finishes_without_candidate(chapter_improvement, monkeypatch):
    decision = {"outcome": "no_change", "summary": "专用评分说明", "evidence_samples": ["original-1"]}
    improve = Mock(side_effect=[clients.ServiceUnavailable("模型暂时不可用"), decision])
    dispatch = Mock(side_effect=AssertionError("no candidate to schedule"))
    monkeypatch.setattr(agent, "improve_candidate", improve)
    monkeypatch.setattr(runner, "_add_samples", dispatch)

    runner.advance_one(chapter_improvement)
    recovering = repo.get_run(chapter_improvement)
    assert recovering["status"] == "recovering"
    assert recovering["rounds"][0]["status"] == "evolving"
    runner.advance_one(chapter_improvement)
    runner.advance_one(chapter_improvement)

    finished = repo.get_run(chapter_improvement)
    assert finished["status"] == "completed" and finished["outcome"] == "no_change"
    assert finished["current_round"] == 1 and len(finished["rounds"]) == 1
    assert finished["best_candidate"] is None
    assert [item["id"] for item in finished["samples"]] == ["original-1"]
    record = finished["rounds"][0]
    assert record["status"] == "completed" and record["source_commit"] is None
    assert {key: record[key] for key in decision} == decision
    assert finished["end_reason"] == "已核查原版，未找到有证据支持的改动。"
    assert all(decision["summary"] not in event["message"] for event in finished["events"])
    reader_view = api.public_run(finished, can_read_content=False)
    admin_view = api.public_run(finished, can_read_content=True)
    assert reader_view["rounds"][0]["outcome"] == "no_change"
    assert reader_view["end_reason"] == finished["end_reason"]
    assert reader_view["rounds"][0]["summary"] == ""
    assert decision["summary"] in admin_view["end_reason"]
    assert not reader_view["can_publish"] and not admin_view["can_publish"]
    assert improve.call_count == 2
    dispatch.assert_not_called()


@pytest.mark.parametrize("incomplete", [True, False])
def test_chapter_improvement_failure_does_not_create_another_round(chapter_improvement, monkeypatch, incomplete):
    cause = agent.IncompleteImprovement("缺少改动报告") if incomplete else ValueError("候选装配失败")
    improve = Mock(side_effect=cause)
    monkeypatch.setattr(agent, "improve_candidate", improve)

    runner.advance_one(chapter_improvement)
    runner.advance_one(chapter_improvement)

    finished = repo.get_run(chapter_improvement)
    assert finished["status"] == "failed"
    assert finished["current_round"] == 1 and len(finished["rounds"]) == 1
    record = finished["rounds"][0]
    assert record["status"] == "failed"
    assert str(cause) in record["error"]
    assert str(cause) not in finished["end_reason"]
    assert ("改进未完成" in finished["end_reason"]) is incomplete
    improve.assert_called_once()


def test_stop_takes_precedence_over_no_change(chapter_improvement, monkeypatch):
    def stop_and_finish(*_):
        runner.request_stop(chapter_improvement)
        return {"outcome": "no_change", "summary": "保持原版", "evidence_samples": ["original-1"]}

    monkeypatch.setattr(agent, "improve_candidate", stop_and_finish)
    monkeypatch.setattr(runner, "start_worker", lambda _: None)
    runner.advance_one(chapter_improvement)
    runner.advance_one(chapter_improvement)

    finished = repo.get_run(chapter_improvement)
    assert finished["status"] == "stopped"
    assert "outcome" not in finished
