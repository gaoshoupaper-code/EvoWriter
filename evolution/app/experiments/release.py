"""Publish the exact verified candidate with production CAS and runtime confirmation."""
from __future__ import annotations

from app.experiments import clients, repo


def _original_production(run: dict) -> dict:
    return run.get("production_at_start", run["baseline"])


def check_stale(run: dict) -> bool:
    expected = _original_production(run)
    current = clients.production()
    if current["commit"] == expected["commit"] and current["version"] == expected["version"]:
        return False
    run.update(status="stale", stale_reason="生产版本已变化；候选过期，请重新发起验证。")
    repo.event(run, "stale", run["stale_reason"])
    repo.put_run(run)
    return True


def _confirm(run: dict, response: dict, *, restoring: bool = False) -> dict:
    commit = response["commit"]
    try:
        observed = clients.runtime()
        expected_digest = response.get("artifact_digest")
        matches = bool(expected_digest and observed.get("artifact_digest")) and observed.get("commit") == commit
        matches = matches and observed.get("artifact_digest") == expected_digest
        if not restoring:
            matches = matches and expected_digest == run["best_candidate"]["digest"]
    except clients.ServiceUnavailable:
        matches = False
        observed = {}
    if matches:
        run.update(status="restored" if restoring else "activated", publish_status="restored" if restoring else "activated",
                   loaded_revision=commit, load_error=None)
        if restoring:
            run["rollback_status"] = "restored"
        repo.event(run, run["status"], "执行端已确认恢复旧版。" if restoring else "执行端已确认加载本次验证候选。")
    else:
        run.update(status="publish_failed", publish_status="unconfirmed", load_error="生产指针已更新，但执行端尚未确认加载目标版本。")
        if restoring:
            run["rollback_status"] = "failed"
        repo.event(run, "activation_unconfirmed", run["load_error"])
    repo.put_run(run)
    return run


def publish_run(run_id: str, actor: str) -> dict:
    with repo.run_lock(run_id):
        run = repo.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run["status"] == "activated":
            return run
        if run["status"] not in {"awaiting_review", "publish_failed", "publishing"} or not run.get("report", {}).get("passed") or not run.get("best_candidate"):
            raise ValueError("仅通过完整终验且未过期的固定候选可发布")
        candidate = run["best_candidate"]
        if run.get("published"):
            current = clients.production()
            if current["commit"] != run["published"]["commit"] or current["version"] != run["published"]["version"]:
                run.update(status="stale", stale_reason="发布后生产版本再次变化；拒绝覆盖。")
                repo.put_run(run)
                raise ValueError(run["stale_reason"])
            return _confirm(run, run["published"])
        if check_stale(run):
            raise ValueError(run["stale_reason"])
        expected = _original_production(run)
        run.update(status="publishing", phase="publish", publish_status="publishing", approved_commit=candidate["commit"], approved_by=actor)
        repo.event(run, "approved", f"人工批准固定候选 {candidate['commit']}。")
        repo.put_run(run)
        try:
            response = clients.promote({"source_commit": candidate["commit"], "version_note": f"实验 {run_id} 已通过独立终验",
                "expected_production_commit": expected["commit"], "expected_production_version": expected["version"]})
            if response["commit"] != candidate["commit"] or response.get("artifact_digest") != candidate["digest"]:
                raise ValueError("实际发布候选与通过验证的候选不一致")
            run["published"] = response
            repo.put_run(run)
            return _confirm(run, response)
        except Exception as exc:
            # A timed-out promote might already have moved the pointer. Record it
            # before offering restore; never attempt an unconditional second promotion.
            try:
                current = clients.production()
                if current["commit"] == candidate["commit"] and current["version"] != expected["version"]:
                    run["published"] = current
                elif current["commit"] != expected["commit"] or current["version"] != expected["version"]:
                    run.update(status="stale", publish_status="failed", stale_reason="生产版本在发布期间变化；候选已过期。")
                    repo.event(run, "stale", run["stale_reason"])
                    repo.put_run(run)
                    return run
            except Exception:
                pass
            run.update(status="publish_failed", publish_status="failed", load_error=f"发布失败：{type(exc).__name__}")
            repo.event(run, "publish_failed", run["load_error"])
            repo.put_run(run)
            return run


def restore_run(run_id: str, actor: str) -> dict:
    with repo.run_lock(run_id):
        run = repo.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.get("rollback_status") == "restored":
            return run
        if run["status"] not in {"publish_failed", "activated"} or not run.get("published"):
            raise ValueError("当前没有可恢复的本次发布记录")
        previous = _original_production(run)
        published = run["published"]
        run["rollback_status"] = "restoring"
        repo.event(run, "restore_requested", f"人工请求恢复原生产版本，操作者 {actor}。")
        repo.put_run(run)
        try:
            response = clients.restore({"source_commit": previous["commit"],
                "expected_production_commit": published["commit"], "expected_production_version": published["version"],
                "version_note": f"恢复实验 {run_id} 发版前的生产代码"})
            if response["commit"] != previous["commit"]:
                raise ValueError("恢复版本不一致")
            run["restored"] = response
            return _confirm(run, response, restoring=True)
        except Exception as exc:
            run.update(rollback_status="failed", load_error=f"恢复失败：{type(exc).__name__}")
            repo.event(run, "restore_failed", run["load_error"])
            repo.put_run(run)
            return run
