"""发版原语：promote 晋升 + executor reload 通知（FR-002，Phase A 由 evolution 编排调）。

promote 顺序（保证失败不留半晋升状态）：
1. 门禁自查：重跑 probe（比缓存「最近一次 probe 结果」更强——bare repo 可能
   被重新 push 过，重跑拿到的是当下事实）。rejected → ProbeRejected（HTTP 409）。
2. 打包 artifact（幂等，digest 固化）。此时不动账本——打包失败不影响账本一致性。
3. 账本单事务晋升（version 追加 + 旧 production 标 superseded + manifest 补
   digest/转 production + meta 指针切换）。
4. 通知 executor reload（POST /internal/platform/reload）。重试 3 次（间隔 2s）；
   全部失败只记 error 日志，promote 仍成功（reload_notified=False）——
   executor 侧还有冷启动对账兜底（GET /api/production），通知不是强一致链路。
"""
from __future__ import annotations

import logging
import time

import httpx

from contracts.platform import HarnessReloadNotice, PromoteResult

from app.agent.git_checkout import cleanup_checkout
from app.agent.probe import probe_candidate
from app.artifacts import build_artifact
from app.core.ledger import ledger_from_settings
from app.core.settings import get_settings

logger = logging.getLogger("platform.release")

# reload 通知：最多尝试 3 次，间隔 2s
NOTIFY_ATTEMPTS = 3
NOTIFY_INTERVAL_SECONDS = 2.0
NOTIFY_TIMEOUT_SECONDS = 5.0


class ProbeRejected(RuntimeError):
    """promote 前置门禁未过（router 转 HTTP 409）。"""


def promote(source_commit: str, version_note: str = "") -> PromoteResult:
    """晋升生产：门禁 → 打包 → 账本事务 → reload 通知。

    门禁与打包共用同一个 probe checkout（keep_checkout=True）——
    门禁语义不变（仍真实装配），只省掉 promote 里重复的一次 clone。
    """
    probe, checkout = probe_candidate(source_commit, keep_checkout=True)
    if probe.status != "ready":
        # 失败路径 probe_candidate 内部已清理 checkout（返回 None）
        raise ProbeRejected(f"probe 未通过，拒绝晋升: {probe.reason}")

    try:
        built = build_artifact(source_commit, source_dir=checkout)
    finally:
        # 复用的 checkout 打包完即弃，不留临时目录
        cleanup_checkout(checkout)

    ledger = ledger_from_settings()
    version = ledger.apply_promotion(
        commit=source_commit,
        note=version_note,
        artifact_digest=built.meta.digest,
        fingerprint=built.fingerprint,
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
                return True
            logger.warning(
                "reload 通知非 2xx (attempt %d/%d): status=%s",
                attempt, NOTIFY_ATTEMPTS, response.status_code,
            )
        except httpx.HTTPError as exc:
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


__all__ = ["ProbeRejected", "promote", "notify_reload"]
