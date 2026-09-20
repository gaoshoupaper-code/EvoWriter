"""平台控制面契约（共享包，仅依赖 pydantic）。

Platform 是独立控制面服务：版本账本、发版门禁、artifact 分发、Run 绑定签发、
resume 兼容判定。本包定义三端（platform / executor / evolution）通信的「数据形状」，
作为单一真源，延续 contracts 零业务依赖铁律。

涉及的端点（挂在 platform 服务的 /api 路由下，内网信任域，不暴露公网）：
- GET  /api/production                     当前生产版本状态（Runtime 启动/对账用）
- GET  /api/versions                       账本版本列表 + production 指针（评测侧读）
- GET  /api/artifacts/{commit}/meta        artifact 元数据（digest/大小）
- GET  /api/artifacts/{commit}/download    artifact 下载（Runtime 校验 digest 后装配）
- POST /api/bindings                       Run 绑定签发（Runtime 每次 Run 开始时调）
- GET  /api/bindings/{trace_id}            绑定查询（追溯当时装配条件）
- GET  /api/bindings/{trace_id}/resume-check   resume 兼容门禁（HITL 恢复前调）
- POST /api/release/probe                  发版门禁：候选真实装配（evolution 编排调）
- POST /api/release/promote                晋升生产 + 打包 artifact + 通知 Runtime reload

设计依据：需求 REQ-20260919-202344（DEC-002/003/006/007/011/012）。
"""

from contracts.platform.entities import (
    BindingRecord,
    LlmConfigSnapshot,
    ManifestRecord,
    SurfaceFingerprint,
)
from contracts.platform.api import (
    ArtifactMeta,
    BindingAck,
    BindingCreate,
    HarnessReloadNotice,
    ProbeRequest,
    ProbeResult,
    ProductionStatus,
    PromoteRequest,
    PromoteResult,
    ResumeCheck,
    VersionLine,
    VersionsStatus,
)

__all__ = [
    "ArtifactMeta",
    "BindingAck",
    "BindingCreate",
    "BindingRecord",
    "HarnessReloadNotice",
    "LlmConfigSnapshot",
    "ManifestRecord",
    "ProbeRequest",
    "ProbeResult",
    "ProductionStatus",
    "PromoteRequest",
    "PromoteResult",
    "ResumeCheck",
    "SurfaceFingerprint",
    "VersionLine",
    "VersionsStatus",
]
