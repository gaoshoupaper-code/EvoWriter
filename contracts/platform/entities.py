"""平台账本实体形状（Manifest / 绑定记录 / surface 指纹）。

需求 REQ-20260919-202344 第 3 章数据模型：
- Manifest = 一次运行的完整装配条件声明（harness commit + LLM 快照 + surface 指纹），
  不可变、被 Run 引用。
- 绑定记录 = trace_id ↔ manifest 的关联（Run 开始时签发，恢复时找回）。
- surface 指纹 = harness 包按 A/B/C 三层的文件级 sha256 清单，
  兼容门禁据此判定两版差异落在哪层（DEC-006 激活 SchemaLock）。

安全边界（需求风险红线）：LLM 快照不得含明文凭据，只存配置与 key 引用。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from contracts.surface_types import SurfaceLayer


class SurfaceFingerprint(BaseModel):
    """harness 包的三层文件指纹（rel_path → sha256hex）。

    提取规则（Platform 侧静态计算，规则可解释可测试）：
    - a_text: prompts/ 与 skills/ 下的文本文件（纯 prompt/skill 改动不改 State schema）
    - b_param: 包内 json/yaml/toml 配置文件（行为参数）
    - c_code: middleware/ 及包内其余 .py（含 state_schema 的代码）

    兼容判定（DEC-003/006）：两版指纹 diff 后，只有 a_text 变化 → 兼容；
    b_param / c_code 任何增删改 → 不兼容。新增/删除文件同样计入变化。
    """

    a_text: dict[str, str] = Field(default_factory=dict)
    b_param: dict[str, str] = Field(default_factory=dict)
    c_code: dict[str, str] = Field(default_factory=dict)

    def layer_for(self, rel_path: str) -> SurfaceLayer | None:
        """返回文件归属层；不在指纹中返回 None（两版都无此文件的未变路径）。"""
        if rel_path in self.a_text:
            return SurfaceLayer.A_TEXT
        if rel_path in self.b_param:
            return SurfaceLayer.B_PARAM
        if rel_path in self.c_code:
            return SurfaceLayer.C_CODE
        return None


class LlmConfigSnapshot(BaseModel):
    """Run 绑定时的 LLM 配置快照（脱敏，DEC-011）。

    不含明文 api_key（需求风险红线）：Runtime 装配时凭据仍走受控通道现取，
    快照只保证「记录本次实际采用的模型与端点」供追溯与复现。
    """

    model: str
    base_url: str
    api_key_ref: str | None = None  # 凭据引用（Phase A：evolution 配置条目标识）
    source: str = "evolution"  # 治理源（Phase A 在 evolution，Phase B 迁 platform）

    def redacted(self) -> dict[str, str | None]:
        """对外展示用的脱敏视图。"""
        return {
            "model": self.model,
            "base_url": self.base_url,
            "api_key_ref": self.api_key_ref,
            "source": self.source,
        }


class ManifestRecord(BaseModel):
    """版本账本中的 Manifest 实体（不可变，DEC-001）。

    发版晋升时由 Platform 生成（harness_commit + artifact 摘要 + surface 指纹）；
    Run 绑定时引用。llm 快照不进 Manifest 实体——LLM 配置随治理源独立变更，
    属于绑定时的 Run 级条件，记录在绑定上。
    """

    manifest_id: int
    harness_commit: str
    artifact_digest: str | None = None
    surface_fingerprint: SurfaceFingerprint
    status: str = "production"  # production / superseded
    created_at: str


class BindingRecord(BaseModel):
    """Run ↔ Manifest 绑定记录（Run 开始时签发，幂等去重键 = trace_id）。

    degraded=True 表示签发时 Platform 不可达、Runtime 用本地缓存兜底（DEC-008
    fail-static），恢复后补账不覆盖既有字段。
    """

    trace_id: str
    manifest_id: int
    harness_commit: str
    llm_config: LlmConfigSnapshot | None = None
    run_purpose: str = "production"  # production / optimization（A/B 实验）
    degraded: bool = False
    runtime_identity_digest: str | None = None
    bound_at: str
    status: str = "active"  # active / finished / incompatible_resume / legacy
