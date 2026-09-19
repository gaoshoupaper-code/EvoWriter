"""Platform 服务 API 请求/响应模型（pydantic）。

三端通信形状的单一真源：
- executor（Runtime）：拉生产状态、下载 artifact、签发绑定、resume 门禁
- evolution（编排方，Phase A）：调门禁 probe、调晋升 promote
- platform → executor：reload 通知（晋升后触发 Runtime 热加载）
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from contracts.platform.entities import (
    BindingRecord,
    LlmConfigSnapshot,
    SurfaceFingerprint,
)


# ── 生产状态与 artifact ──────────────────────────────────────────


class ProductionStatus(BaseModel):
    """GET /api/production 的响应：当前生产版本（Runtime 冷启动对账源）。"""

    version: int
    commit: str
    artifact_digest: str | None = None
    surface_fingerprint: SurfaceFingerprint
    promoted_at: str


class ArtifactMeta(BaseModel):
    """GET /api/artifacts/{commit}/meta 的响应（下载前校验元数据）。"""

    commit: str
    digest: str
    size_bytes: int
    file_count: int
    built_at: str


# ── Run 绑定（FR-004）────────────────────────────────────────────


class BindingCreate(BaseModel):
    """POST /api/bindings 的请求体（Runtime 每次 Run 开始时签发）。

    幂等：同 trace_id 重复签发返回既有记录，不重复创建（fail-static 补账
    依赖此语义，需求风险「补账冲突」处置）。
    """

    trace_id: str = Field(min_length=1)
    harness_commit: str = Field(min_length=1)
    llm_config: LlmConfigSnapshot | None = None
    run_purpose: str = "production"
    degraded: bool = False
    runtime_identity_digest: str | None = None


class BindingAck(BaseModel):
    """POST /api/bindings 的响应（确认 + 供 Runtime 记 trace 的回执）。"""

    binding: BindingRecord
    created: bool  # False = 幂等命中既有绑定


# ── resume 兼容门禁（FR-005，DEC-003/006）─────────────────────────


class ResumeCheck(BaseModel):
    """GET /api/bindings/{trace_id}/resume-check 的响应。

    decision 判定规则：绑定版本与当前生产版本的 surface 指纹 diff——
    仅 a_text 层变化 → compatible（用当前版继续）；
    b_param/c_code 任何变化或指纹缺失 → incompatible（拒绝恢复）。
    判定过程自身失败按 incompatible 保守处理（需求风险处置）。
    """

    trace_id: str
    decision: str  # compatible / incompatible
    reason: str
    bound_commit: str
    current_commit: str
    changed_layers: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)


# ── 发版原语（FR-002，Phase A 由 evolution 编排调用）──────────────


class ProbeRequest(BaseModel):
    """POST /api/release/probe 的请求体：候选 commit 门禁装配。"""

    source_commit: str = Field(min_length=1)


class ProbeResult(BaseModel):
    """门禁结果：status=ready 才允许 promote。"""

    status: str  # ready / rejected
    reason: str | None = None
    runtime_identity: dict | None = None


class PromoteRequest(BaseModel):
    """POST /api/release/promote 的请求体：晋升生产。"""

    source_commit: str = Field(min_length=1)
    version_note: str = ""


class PromoteResult(BaseModel):
    """晋升结果：账本已更新、artifact 已打包、reload 通知已尝试。"""

    version: int
    commit: str
    artifact_digest: str | None
    reload_notified: bool


# ── reload 通知（Platform → executor，晋升后触发）────────────────


class HarnessReloadNotice(BaseModel):
    """POST executor /internal/platform/reload 的请求体。

    executor 收到后从 Platform 下载该 commit 的 artifact、校验 digest、
    热重载生产包（替代旧的 git pull + reload 链路，DEC-012）。
    """

    version: int
    commit: str
    artifact_digest: str | None = None
