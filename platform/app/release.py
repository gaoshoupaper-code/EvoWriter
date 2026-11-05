"""发版原语：promote 晋升 + executor reload 通知（FR-002，Phase A 由 evolution 编排调）。

promote 顺序（保证失败不留半晋升状态）：
1. 门禁自查：重跑 probe（比缓存「最近一次 probe 结果」更强——bare repo 可能
   被重新 push 过，重跑拿到的是当下事实）。rejected → ProbeRejected（HTTP 409）。
2. 打包 artifact（幂等，digest 固化）。此时不动账本——打包失败不影响账本一致性。
3. 账本单事务晋升（version 追加 + 旧 production 标 superseded + manifest 补
   digest/转 production + meta 指针切换）。
4. 通知 executor reload（POST /internal/platform/reload）。重试 3 次（间隔 2s）；
   只有实际 commit 回执一致才确认生效。未确认时账本晋升保留，
   activation_status=unconfirmed；调用方不可将它展示为已生效。
"""
from __future__ import annotations

import logging
import threading
import time

import httpx

from contracts.platform import ArtifactMeta, HarnessReloadNotice, PromoteResult

from app.agent.git_checkout import cleanup_checkout
from app.agent.probe import probe_candidate
from app.artifacts import build_artifact
from app.core.ledger import ProductionChanged, ledger_from_settings
from app.core.settings import get_settings

logger = logging.getLogger("platform.release")

# reload 通知：最多尝试 3 次，间隔 2s
NOTIFY_ATTEMPTS = 3
NOTIFY_INTERVAL_SECONDS = 2.0
NOTIFY_TIMEOUT_SECONDS = 5.0
_RELEASE_LOCK = threading.RLock()


class ProbeRejected(RuntimeError):
    """promote 前置门禁未过（router 转 HTTP 409）。"""


def prepare_candidate(source_commit: str) -> ArtifactMeta:
    """Validate and package a candidate; never change production or reload."""
    probe, checkout = probe_candidate(source_commit, keep_checkout=True)
    if probe.status != "ready":
        raise ProbeRejected(f"candidate probe rejected: {probe.reason}")
    try:
        built = build_artifact(source_commit, source_dir=checkout)
    finally:
        cleanup_checkout(checkout)
    if built.fingerprint is None:
        raise RuntimeError("candidate fingerprint unavailable")
    ledger = ledger_from_settings()
    manifest = ledger.create_manifest(source_commit, built.fingerprint)
    if manifest.artifact_digest and manifest.artifact_digest != built.meta.digest:
        raise RuntimeError("candidate artifact digest changed")
    ledger.fill_artifact_digest(source_commit, built.meta.digest)
    return built.meta


def promote(
    source_commit: str, version_note: str = "", *,
    expected_production_commit: str | None = None,
    expected_production_version: int | None = None,
) -> PromoteResult:
    # Serialize notifications as well as pointer writes: an older request must
    # not reload its package after a newer promotion has already activated.
    with _RELEASE_LOCK:
        return _promote(
            source_commit, version_note,
            expected_production_commit=expected_production_commit,
            expected_production_version=expected_production_version,
        )


def _promote(
    source_commit: str, version_note: str = "", *,
    expected_production_commit: str | None = None,
    expected_production_version: int | None = None,
) -> PromoteResult:
    """晋升生产：门禁 → 打包 → 账本事务 → reload 通知。

    门禁与打包共用同一个 probe checkout（keep_checkout=True）——
    门禁语义不变（仍真实装配），只省掉 promote 里重复的一次 clone。
    """
    ledger = ledger_from_settings()
    current = ledger.get_production()
    if (
        expected_production_commit is not None
        and (current is None or current.commit != expected_production_commit)
    ) or (
        expected_production_version is not None
        and (current is None or current.version != expected_production_version)
    ):
        raise ProductionChanged("生产版本已变化，候选已过期，请重新验证")
    probe, checkout = probe_candidate(source_commit, keep_checkout=True)
    if probe.status != "ready":
        # 失败路径 probe_candidate 内部已清理 checkout（返回 None）
        raise ProbeRejected(f"probe 未通过，拒绝晋升: {probe.reason}")

    try:
        built = build_artifact(source_commit, source_dir=checkout)
    finally:
        # 复用的 checkout 打包完即弃，不留临时目录
        cleanup_checkout(checkout)

    version = ledger.apply_promotion(
        commit=source_commit,
        note=version_note,
        artifact_digest=built.meta.digest,
        fingerprint=built.fingerprint,
        expected_production_commit=expected_production_commit,
        expected_production_version=expected_production_version,
    )
    logger.info(
        "晋升完成: v%d commit=%s digest=%s",
        version, source_commit[:12], built.meta.digest[:12],
    )

    reload_notified = notify_reload(
        HarnessReloadNotice(
            version=version, commit=source_commit, artifact_digest=built.meta.digest
        )
    )
    return PromoteResult(
        version=version,
        commit=source_commit,
        artifact_digest=built.meta.digest,
        reload_notified=reload_notified,
        activation_status="activated" if reload_notified else "unconfirmed",
        observed_commit=source_commit if reload_notified else None,
    )


def notify_reload(notice: HarnessReloadNotice) -> bool:
    """POST executor /internal/platform/reload。成功返回 True；重试耗尽 False。"""
    settings = get_settings()
    url = f"{settings.executor_url.rstrip('/')}/internal/platform/reload"
    headers = {"X-Notify-Token": settings.notify_token} if settings.notify_token else {}
    payload = notice.model_dump()

    for attempt in range(1, NOTIFY_ATTEMPTS + 1):
        try:
            response = httpx.post(url, json=payload, headers=headers,
                                  timeout=NOTIFY_TIMEOUT_SECONDS)
            if response.status_code < 400:
                body = response.json()
                if (
                    isinstance(body, dict)
                    and body.get("status") == "reloaded"
                    and body.get("commit") == notice.commit
                ):
                    return True
                logger.error("reload 回执身份不一致，不能确认生效: version=%s", notice.version)
            logger.warning(
                "reload 通知非 2xx (attempt %d/%d): status=%s",
                attempt, NOTIFY_ATTEMPTS, response.status_code,
            )
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning(
                "reload 通知失败 (attempt %d/%d): %s", attempt, NOTIFY_ATTEMPTS, exc
            )
        if attempt < NOTIFY_ATTEMPTS:
            time.sleep(NOTIFY_INTERVAL_SECONDS)

    logger.error(
        "reload 通知全部失败（%d 次）: version=%d commit=%s——executor 将靠冷启动对账兜底",
        NOTIFY_ATTEMPTS, notice.version, notice.commit,
    )
    return False


__all__ = ["ProbeRejected", "prepare_candidate", "promote", "notify_reload"]
