"""生产版本周期对账协程（Phase A fail-static 的恢复侧，FR-003/FR-004）。

reload 通知（晋升时 Platform POST /internal/platform/reload）是版本跟进的
主通道，但通知失败/网络分区时 executor 会一直跑旧版。本协程每 600s 向
Platform 对账一次：生产 commit 与当前加载的不一致 → reload_current 拉新版。

对账/重载失败静默等下一轮——对账是兜底通道，异常不允许抛进 lifespan；
尚未加载过生产包（production_commit 为空）时不对账，由首个 Run 的
load_current_package 负责初次加载。
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

logger = logging.getLogger("writer.production_reconcile")

_RECONCILE_INTERVAL = 600.0  # 10min：reload 通知是主通道，对账只需低频兜底


async def reconcile_production_once() -> None:
    """单轮对账：Platform 生产 commit ≠ 当前加载 → 热加载新版。"""
    from app.platform.agent import loader
    from app.platform.agent.artifact_client import get_artifact_client

    current = loader.production_commit()
    if not current:
        return
    # HTTP 是同步调用，放线程池避免阻塞事件循环
    status = await asyncio.to_thread(get_artifact_client().current_production)
    if status.commit and status.commit != current:
        logger.info(
            "生产对账发现新版本: %s → %s，触发热加载", current, status.commit,
        )
        # 带账本 digest 装配：与 reload 通知同语义，通知/账本不一致时拒绝
        await asyncio.to_thread(
            loader.reload_current, status.commit, status.artifact_digest,
        )


async def _reconcile_loop() -> None:
    while True:
        await asyncio.sleep(_RECONCILE_INTERVAL)
        try:
            await reconcile_production_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 —— 对账失败静默等下一轮
            logger.debug("生产对账失败（下轮重试）", exc_info=True)


_task: asyncio.Task | None = None


def start_production_reconcile_loop() -> None:
    """启动对账后台协程（lifespan 调用，幂等）。须在事件循环线程内调用。"""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_reconcile_loop())
        logger.info("生产版本对账已启动（间隔 %.0fs）", _RECONCILE_INTERVAL)


async def aclose_production_reconcile_loop() -> None:
    """关闭对账协程（lifespan shutdown 调用）。"""
    global _task
    if _task is not None:
        _task.cancel()
        with suppress(asyncio.CancelledError):
            await _task
        _task = None


__all__ = [
    "reconcile_production_once",
    "start_production_reconcile_loop",
    "aclose_production_reconcile_loop",
]
