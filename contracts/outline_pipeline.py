"""outline 管线状态契约（REQ-20261011-142529 FR-003/004，DEC-006/008/020）。

`outline/_pipeline.json` 是双 agent 管线的执行编排状态。两条边界：

  - 编排归 executor 代码（DEC-020）：状态机转移（阶段推进 / 回炉三段 /
    会话序号）由 executor 管线层驱动；harness 侧仅 submit_rework_ticket
    工具在用户拍板后写入回炉段状态（工具在 executor 进程内运行）。
  - 产物目录即进度真相源（FR-001）：本文件是可重建的加速层——缺失或
    损坏时按 outline/ 目录扫描重建，不阻塞创作主流程。

executor 管线层与 harness 工具共用本模块（跨端共享契约，零业务依赖）。
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1
STATE_RELPATH = "outline/_pipeline.json"
TICKET_DIR = "outline/rework"

PHASE_DESIGN = "storydesign"
PHASE_STRUCTURE = "storystructure"

# 回炉三段（DEC-008/DEC-020 三停）
REWORK_AWAITING = "awaiting_approval"   # 提案已交用户拍板（interrupt 挂起）
REWORK_DESIGN = "design_revising"       # 已批准：设计端执行上游修订
REWORK_IMPACT = "impact_revising"       # 上游已修订：结构端做波及修订
REWORK_STAGES = (REWORK_AWAITING, REWORK_DESIGN, REWORK_IMPACT)


class ReworkState(BaseModel):
    ticket: str = Field(description="工单编号，如 '001'")
    title: str = ""
    stage: str = Field(description=f"回炉阶段：{'/'.join(REWORK_STAGES)}")


class PipelineState(BaseModel):
    schema_version: int = SCHEMA_VERSION
    phase: str = PHASE_DESIGN
    step: int = Field(default=0, description="当前推进到的步骤号 0-7（目录扫描校准）")
    rework: ReworkState | None = None
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


def next_ticket_id(workspace_path: Path) -> str:
    """下一个工单编号（目录内已有 NNN-提案.md 的最大号 + 1）。"""
    tickets = Path(workspace_path) / TICKET_DIR
    max_id = 0
    if tickets.is_dir():
        for f in tickets.glob("*.md"):
            m = re.match(r"^(\d+)-", f.name)
            if m:
                max_id = max(max_id, int(m.group(1)))
    return f"{max_id + 1:03d}"


def write_rework_ticket(
    workspace_path: Path, ticket_id: str, title: str, proposal_md: str
) -> Path:
    """落盘回炉工单文件（outline/rework/NNN-提案.md）。系统机制写入，不经 agent 白名单。"""
    tickets = Path(workspace_path) / TICKET_DIR
    tickets.mkdir(parents=True, exist_ok=True)
    path = tickets / f"{ticket_id}-提案.md"
    path.write_text(proposal_md, encoding="utf-8")
    return path


__all__ = [
    "PHASE_DESIGN",
    "PHASE_STRUCTURE",
    "REWORK_AWAITING",
    "REWORK_DESIGN",
    "REWORK_IMPACT",
    "REWORK_STAGES",
    "ReworkState",
    "SCHEMA_VERSION",
    "STATE_RELPATH",
    "TICKET_DIR",
    "PipelineState",
    "load_pipeline_state",
    "next_ticket_id",
    "save_pipeline_state",
    "write_rework_ticket",
]
