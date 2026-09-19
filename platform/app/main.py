"""platform 控制面入口（FastAPI）。

main.py 只做三件事：app 实例化 + lifespan（账本建表 + FR-001 初始导入）+
挂 /api 路由。所有配置读取发生在请求期 / lifespan 期（不在 import 期），
测试才能通过 env + get_settings.cache_clear() 实现按例隔离。
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.agent.git_checkout import read_bare_registry
from app.core.ledger import ledger_from_settings
from app.routers.api import router as api_router

logger = logging.getLogger("platform.main")


def bootstrap_ledger() -> None:
    """建表 + FR-001 初始导入（幂等）。

    导入失败（registry 缺 commit_hash / production 指针不合法）直接抛出
    → lifespan 失败 → 服务拒绝启动。账本导入是事务性的，不留半导入状态。
    bare repo 无可读 registry（未初始化 / 空）则跳过导入，允许空账本启动。
    """
    ledger = ledger_from_settings()
    ledger.ensure_schema()
    raw = read_bare_registry()
    if raw is None:
        logger.info("bare repo 无可读 registry.json，跳过初始导入")
        return
    try:
        registry = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"bare repo registry.json 无法解析: {exc}") from exc
    imported = ledger.import_registry(registry)
    if imported:
        logger.info("FR-001 初始导入完成: %d 个版本", imported)
    else:
        logger.info("账本非空，跳过初始导入（幂等）")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bootstrap_ledger()
    yield


app = FastAPI(
    title="Writer Platform 控制面",
    version="0.1.0",
    description="版本账本 / 发版门禁 / artifact 分发 / Run 绑定 / resume 兼容判定",
    lifespan=lifespan,
)

app.include_router(api_router)
