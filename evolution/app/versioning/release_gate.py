"""发版门禁与晋升的 Platform 原语客户端（Phase A，REQ-20260919-202344）。

Phase A 起 evolution 只做发版编排，原语归 Platform 服务（platform/app/release.py）：
  - probe_candidate → POST {platform_url}/api/release/probe
    （Platform 在 bare repo 干净 checkout 上做真实最小装配，门禁不再寄生 executor）
  - promote_release → POST {platform_url}/api/release/promote
    （Platform 内部自查 probe → 账本晋升 → 打包 artifact → 通知 executor reload
    带重试，全包；evolution 不再写本地 registry.json、不再直连 executor reload）

旧链路退役：executor /internal/harness/probe 已删（本模块旧版曾直连）；
registry.json 本地晋升写线同步退役（只读，Platform 账本是仲裁源）。
"""
from __future__ import annotations

import httpx

from contracts.platform import ProbeRequest, ProbeResult, PromoteRequest, PromoteResult

from app.core.settings import settings

# probe 在 Platform 内部做干净 checkout + 子进程装配（硬超时 120s），
# 客户端预算略高，避免 Platform 还在装配就被 evolution 掐断。
PROBE_TIMEOUT_SECONDS = 150.0
# promote 内部会重跑一次 probe + 打包 artifact，预算按「再跑一遍门禁」给足。
PROMOTE_TIMEOUT_SECONDS = 300.0


class ReleasePromoteError(RuntimeError):
    """Platform promote 失败（非 2xx 或传输异常）。

    Platform promote 是原子的：失败时账本未动、生产未变，调用方无需本地补偿，
    按激活失败错误路径处理即可。
    """


def _platform_url() -> str:
    return settings.platform_url.rstrip("/")


def probe_candidate(commit_hash: str) -> ProbeResult:
    """调 Platform 门禁原语：候选 commit 干净 checkout 真实装配。

    失败语义与旧版（直连 executor probe）一致：
      - status=rejected → ValueError（由 publish_session 捕获转 409）
      - 网络/HTTP 异常向上抛（由 publish_session 兜底捕获）
    """
    response = httpx.post(
        f"{_platform_url()}/api/release/probe",
        json=ProbeRequest(source_commit=commit_hash).model_dump(),
        timeout=PROBE_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    result = ProbeResult.model_validate(response.json())
    if result.status != "ready":
        raise ValueError(f"candidate clean-checkout probe failed: {result.reason}")
    return result


def promote_release(source_commit: str, version_note: str = "") -> PromoteResult:
    """调 Platform 晋升原语：账本晋升 + artifact 打包 + executor reload 通知。

    Returns:
        PromoteResult：version 为 Platform 账本新分配的版本号，commit 回显
        晋升的源 commit（调用方须与冻结的 source_commit 做一致性断言）。

    Raises:
        ReleasePromoteError: Platform 返回非 2xx，或传输层异常（连接拒绝/超时）。
    """
    try:
        response = httpx.post(
            f"{_platform_url()}/api/release/promote",
            json=PromoteRequest(
                source_commit=source_commit, version_note=version_note
            ).model_dump(),
            timeout=PROMOTE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise ReleasePromoteError(f"platform promote 传输失败: {exc}") from exc
    if not response.is_success:
        raise ReleasePromoteError(
            f"platform promote 失败: HTTP {response.status_code}"
            f" {response.text[:500]}"
        )
    return PromoteResult.model_validate(response.json())


__all__ = [
    "ReleasePromoteError",
    "probe_candidate",
    "promote_release",
    "PROBE_TIMEOUT_SECONDS",
    "PROMOTE_TIMEOUT_SECONDS",
]
