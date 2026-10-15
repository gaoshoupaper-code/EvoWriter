"""outline 管线状态契约（REQ-20261015-161200 FR-002/003，DEC-005）。

`outline/_pipeline.json` 是单主控管线的执行编排状态（会话 thread 派生与
未决拍板定位）。两条边界：

  - 编排归 executor 代码：路由（legacy 只读 / 三步推进）与状态转移由
    executor 管线层驱动；上游修订走主控转委托对话路径（DEC-009），
    不再有回炉状态机与工单工具。
  - 产物目录即进度真相源（FR-001）：本文件是可重建的加速层——缺失或
    损坏时按 outline/ 目录扫描重建，不阻塞创作主流程。

SCHEMA_VERSION=2：七步制状态文件（含 rework 段）加载时按默认状态丢弃。

executor 管线层与 harness 共用本模块（跨端共享契约，零业务依赖）。
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

SCHEMA_VERSION = 2
STATE_RELPATH = "outline/_pipeline.json"

# 三步制相位（目录推导；legacy = 旧版工作区只读模式）
PHASE_WORLDVIEW = "worldview"
PHASE_OUTLINE = "outline"
PHASE_CHAPTERS = "chapters"
PHASE_LEGACY = "legacy"


class PipelineState(BaseModel):
    schema_version: int = SCHEMA_VERSION
    phase: str = PHASE_WORLDVIEW
    sessions: dict[str, int] = Field(default_factory=dict, description="agent → 会话序号（递增）")
    active_threads: dict[str, str] = Field(default_factory=dict, description="agent → 当前会话 thread_id")
    active_traces: dict[str, str] = Field(default_factory=dict, description="agent → 当前 awaiting trace_id（作废定位）")
    updated_at: str = ""


def load_pipeline_state(workspace_path: Path) -> PipelineState:
    """容错读取：缺失 / 损坏 / schema 不符 → 默认状态（可按目录扫描重建）。"""
    path = Path(workspace_path) / STATE_RELPATH
    if not path.is_file():
        return PipelineState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        state = PipelineState.model_validate(raw)
    except Exception:  # noqa: BLE001 — 状态层故障不得中断创作主流程
        return PipelineState()
    if state.schema_version != SCHEMA_VERSION:
        return PipelineState()
    return state


def save_pipeline_state(workspace_path: Path, state: PipelineState) -> None:
    """原子写（临时文件 + replace）；目录不存在则创建。写失败抛 OSError 由调用方兜底。"""
    path = Path(workspace_path) / STATE_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = datetime.now(UTC).isoformat()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


__all__ = [
    "PHASE_CHAPTERS",
    "PHASE_LEGACY",
    "PHASE_OUTLINE",
    "PHASE_WORLDVIEW",
    "PipelineState",
    "SCHEMA_VERSION",
    "STATE_RELPATH",
    "load_pipeline_state",
    "save_pipeline_state",
]
