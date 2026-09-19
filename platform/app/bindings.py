"""Run 绑定签发与 resume 兼容门禁（FR-004 / FR-005）。

绑定幂等铁律：同 trace_id 重复 POST 返回既有记录 + created=False，
不覆盖任何字段——executor fail-static 补账（DEC-008）依赖此语义。

绑定时的 surface 指纹不单独存：通过 harness_commit 关联 manifests 指纹
（数据模型第 3 章：绑定引用 manifest，不复制指纹）。
"""
from __future__ import annotations

import logging

from contracts.platform import BindingAck, BindingCreate, BindingRecord, ResumeCheck

from app.compat import judge_resume
from app.core.ledger import ledger_from_settings
from app.manifests import ensure_manifest

logger = logging.getLogger("platform.bindings")


def issue_binding(req: BindingCreate) -> BindingAck:
    """签发绑定（幂等）。commit 不在账本 → 自动按该 commit 建 manifest（算指纹，不打包）。"""
    ledger = ledger_from_settings()
    existing = ledger.get_binding(req.trace_id)
    if existing is not None:
        return BindingAck(binding=existing, created=False)

    manifest = ensure_manifest(ledger, req.harness_commit)
    record, created = ledger.create_binding(
        trace_id=req.trace_id,
        manifest_id=manifest.manifest_id,
        harness_commit=req.harness_commit,
        llm_config=req.llm_config,
        run_purpose=req.run_purpose,
        degraded=req.degraded,
        runtime_identity_digest=req.runtime_identity_digest,
    )
    if not created:
        logger.info("绑定幂等命中: trace_id=%s", req.trace_id)
    return BindingAck(binding=record, created=created)


def get_binding(trace_id: str) -> BindingRecord | None:
    return ledger_from_settings().get_binding(trace_id)


def resume_check(trace_id: str) -> ResumeCheck | None:
    """resume 兼容门禁。无绑定记录返回 None（router 转 404）。

    当前生产指纹若缺 manifest（FR-001 导入期），先惰性补建——生产 commit 是
    Platform 自己管理的，指纹是纯静态函数，可安全补算；补算失败（bare repo
    异常等）按保守规则走 incompatible。
    """
    ledger = ledger_from_settings()
    binding = ledger.get_binding(trace_id)
    if binding is None:
        return None

    production = ledger.get_production()
    current_commit = production.commit if production else ""
    if production is None:
        return ResumeCheck(
            trace_id=trace_id,
            decision="incompatible",
            reason="当前无生产版本，拒绝恢复",
            bound_commit=binding.harness_commit,
            current_commit="",
        )

    bound_manifest = ledger.get_manifest_by_commit(binding.harness_commit)
    bound_fp = bound_manifest.surface_fingerprint if bound_manifest else None

    current_fp = None
    try:
        current_manifest = ensure_manifest(ledger, production.commit)
        current_fp = current_manifest.surface_fingerprint
    except Exception as exc:  # noqa: BLE001 — 指纹补算失败按缺失保守处理
        logger.warning("生产指纹补算失败: commit=%s err=%s", production.commit, exc)

    verdict = judge_resume(bound_fp, current_fp)
    return ResumeCheck(
        trace_id=trace_id,
        decision=verdict.decision,
        reason=verdict.reason,
        bound_commit=binding.harness_commit,
        current_commit=current_commit,
        changed_layers=verdict.changed_layers,
        changed_files=verdict.changed_files,
    )


__all__ = ["issue_binding", "get_binding", "resume_check"]
