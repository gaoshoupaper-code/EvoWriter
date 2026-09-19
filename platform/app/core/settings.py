"""platform 服务配置（pydantic-settings，env 前缀 PLATFORM_）。

风格照抄 executor/app/platform/core/settings.py：BaseSettings + lru_cache 单例。
所有相对路径（db_path/bare_repo/artifact_dir/checkout_dir）基于仓库根 Writer/
解析——与 executor 的 harness_bare_repo 解析口径一致，部署时容器内工作目录
不同也不会跑偏。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def project_root() -> Path:
    """仓库根 Writer/（本文件在 platform/app/core/settings.py，上三级）。"""
    return Path(__file__).resolve().parents[3]


def resolve_path(p: str) -> Path:
    """相对路径基于仓库根解析，绝对路径原样返回。"""
    path = Path(p)
    if not path.is_absolute():
        path = project_root() / path
    return path.resolve()


class Settings(BaseSettings):
    """platform 服务配置项。

    env 变量名 = PLATFORM_ + 字段名大写（如 PLATFORM_DB_PATH）。
    """

    # 账本数据库（SQLite 单文件）。相对路径基于仓库根。
    db_path: str = "platform/data/platform.db"

    # harness bare repo 路径/URL（与 executor 的 harness_bare_repo 同一仓库）。
    bare_repo: str = "evolution/harness.git"

    # artifact 打包输出目录（{commit}.tar.gz 落这里）。
    artifact_dir: str = "platform/artifacts"

    # 临时 checkout 根目录（probe/打包/算指纹时 clone 到此，用完即清）。
    checkout_dir: str = "platform/.checkouts"

    # executor 服务基址（promote 后发 reload 通知用）。
    executor_url: str = "http://localhost:7788"

    # reload 通知携带的 X-Notify-Token（内网互信 token，留空则不带）。
    notify_token: str = ""

    # artifact 目录保留最近 N 个包（LRU 按文件创建时间清理）。
    artifact_retention: int = 20

    # executor 代码目录（相对仓库根）。生产 harness 包是「薄包装」——assemble
    # 链 import executor 的 app.platform.*（Phase 7 迁移中间态）。probe 门禁在
    # 子进程里装配候选包（见 agent/probe_worker.py：platform 自身顶层包也叫
    # app，同进程会撞名），本目录注入子进程 PYTHONPATH，保证与 Runtime 用
    # 同一份装配代码（DEC-007 门禁语义 + 「probe 环境漂移」风险处置）。
    # Phase B 计划把共享装配构件下沉 contracts 后此项退役。
    executor_code_path: str = "executor"

    model_config = SettingsConfigDict(
        env_prefix="PLATFORM_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """配置单例。测试通过改 env + cache_clear() 实现按例隔离。"""
    return Settings()
