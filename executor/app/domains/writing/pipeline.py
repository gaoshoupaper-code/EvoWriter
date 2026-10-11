"""双 agent 管线编排层（REQ-20261011-142529 FR-002/003/004，DEC-006/008/020）。

编排归 executor 代码（DEC-020）：管「现在轮到哪个 agent、第几步、在等谁」。
产物目录即进度真相源（FR-001）：outline/ 目录扫描定相位；`outline/_pipeline.json`
是可重建的加速层（contracts.outline_pipeline 共享契约，harness 工具同读写）。

职责：
  - 路由：legacy（旧五件套修订模式）/ storydesign / storystructure；
    回炉三段（design_revising / impact_revising）覆盖常规路由。
  - 会话：每 agent 按会话切 thread（DEC-020 第 4 条）——新会话序号递增，
    thread_id = f"{base}:{agent}:{seq}"；resume 沿用 active_threads；
    丢弃重开 = 作废旧 trace + 新会话（不强制回原 thread）。
  - 状态转移：interrupt 时记 active trace；run 完成时推进回炉段、重扫相位。

纯同步模块（唯一副作用是状态文件读写），可独立单测。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from contracts.outline_pipeline import (
    PHASE_DESIGN,
    PHASE_STRUCTURE,
    REWORK_DESIGN,
    REWORK_IMPACT,
    PipelineState,
    load_pipeline_state,
    save_pipeline_state,
)

# 设计端四步产物（缺任一 = 设计端未完成）
DESIGN_FILES = ("00-赛道.md", "01-设计原则.md", "02-冲突引擎.md", "03-力量体系.md")
DESIGN_STEP_LABELS = {
    "00-赛道.md": "第 0 步·赛道确认",
    "01-设计原则.md": "第 1 步·设计原则",
    "02-冲突引擎.md": "第 2 步·冲突引擎",
    "03-力量体系.md": "第 3 步·力量体系",
}

# 用户消息命中设计端关键词 → 路由设计端打磨（结构期改上游创意的唯一常规入口；
# 需要改文件内容的仍走回炉工单——白名单拦得住）
_DESIGN_KEYWORDS = (
    "设计原则", "冲突引擎", "力量体系", "赛道", "境界划分", "金手指",
    "主角卡", "大 BOSS", "大BOSS", "反派递进", "世界观核心",
)


@dataclass
class RouteDecision:
    agent_name: str | None          # None = manifest 首个 main（legacy 兼容）
    thread_id: str                  # langgraph thread（会话派生）
    directive: str                  # 任务书（拼进 user prompt 头部）
    phase: str                      # storydesign / storystructure / legacy
    new_session: bool = False       # 本轮是否新会话（fresh 丢弃重开标记）


def classify_workspace(workspace: Path) -> str:
    """"legacy"（旧五件套）| "pipeline"（新 outline 结构 / 全新）。"""
    workspace = Path(workspace)
    if (workspace / "storyline.md").exists() and not (workspace / "outline").is_dir():
        return "legacy"
    return "pipeline"


def next_design_step(workspace: Path) -> tuple[str, str]:
    """返回 (缺失文件名, 步骤标签)；四步齐备返回 ("", "设计端完成")。"""
    for fname in DESIGN_FILES:
        if not (Path(workspace) / "outline" / fname).is_file():
            return fname, DESIGN_STEP_LABELS[fname]
    return "", "设计端完成"


def scan_phase(workspace: Path) -> str:
    """按目录定相位：00-03 缺任一 → design；齐备 → structure。"""
    fname, _ = next_design_step(workspace)
    return PHASE_DESIGN if fname else PHASE_STRUCTURE


def _progress_desc(workspace: Path) -> str:
    done = [f for f in DESIGN_FILES if (Path(workspace) / "outline" / f).is_file()]
    parts = [DESIGN_STEP_LABELS[f].split("·", 1)[1] for f in done]
    return "、".join(parts) if parts else "尚未开始"


def _session_thread(state: PipelineState, base_thread_id: str, agent: str, *, new: bool) -> str:
    """会话 thread 派生：new=True 递增序号；否则沿用当前（无则新开）。"""
    if new or agent not in state.active_threads:
        seq = state.sessions.get(agent, 0) + 1
        state.sessions[agent] = seq
        state.active_threads[agent] = f"{base_thread_id}:{agent}:{seq}"
    return state.active_threads[agent]


def route(
    workspace: Path,
    base_thread_id: str,
    *,
    user_text: str = "",
    resume_trace: str | None = None,
    fresh: bool = False,
) -> RouteDecision:
    """路由决策（唯一入口）。不写盘——状态落盘由 start_session/finish 调用方驱动。

    Args:
        resume_trace: resume 续跑的 trace_id（有值 = 回到该 agent 当前会话）。
        fresh: 用户显式丢弃重开（作废未决拍板，新会话）。
    """
    workspace = Path(workspace)
    state = load_pipeline_state(workspace)

    # ── legacy：旧五件套 → 设计端（首个 main）自由修订模式 ──
    if classify_workspace(workspace) == "legacy":
        thread_id = _session_thread(state, base_thread_id, "legacy-design", new=True)
        return RouteDecision(
            agent_name=None,
            thread_id=thread_id,
            phase="legacy",
            directive=(
                "【旧版工作区修订模式】本 workspace 是旧版五件套结构（storyline.md / "
                "worldview.md / character/ / object/ / hooks.md），不走七步管线。"
                "按用户要求协助查看与修订既有文件；改前先读、拍板后落盘。"
            ),
            new_session=True,
        )

    agent = PHASE_STRUCTURE
    rework = state.rework

    # ── 回炉三段覆盖路由（DEC-008/020）──
    if rework and rework.stage == REWORK_DESIGN:
        agent = PHASE_DESIGN
        directive = (
            f"【回炉修订·第二停】读 outline/rework/{rework.ticket}-提案.md，"
            "按主文档回炉协议执行上游修订：起草修订方案 → confirm 拍板 → "
            "按批准修订上游文件（不超提案范围）→ 汇报收束。"
        )
    elif rework and rework.stage == REWORK_IMPACT:
        directive = (
            f"【波及修订·第三停】读 outline/rework/{rework.ticket}-提案.md 与"
            "修订后的上游文件，先对照提案核对设计端改了什么，再逐项修订波及"
            "清单内的下游内容（章纲批/台账/卷纲），confirm 拍板后落盘。"
        )
    else:
        phase = scan_phase(workspace)
        wants_design = any(k in (user_text or "") for k in _DESIGN_KEYWORDS)
        if phase == PHASE_DESIGN:
            agent = PHASE_DESIGN
            fname, label = next_design_step(workspace)
            directive = (
                f"【管线任务·设计端】当前进度：{_progress_desc(workspace)}。"
                f"本单元做「{label}」（产物 outline/{fname} 与配套深度文件），"
                "按主文档单元流程：起草 → confirm 拍板 → 批准后落盘 → 停下。"
            )
        elif wants_design:
            agent = PHASE_DESIGN
            directive = (
                "【管线任务·设计端·打磨】用户消息指向设计端产物（00-03 / "
                "character/core / worldview/core.md）。按主文档单元流程打磨对应"
                "文件：起草修订 → confirm 拍板 → 落盘 → 停下。"
            )
        else:
            agent = PHASE_STRUCTURE
            directive = (
                "【管线任务·结构端】扫描 outline/ 目录定位当前进度（目录即进度），"
                "做下一单元：04-总纲 或 05-卷纲 或 06-章纲批（每批 5-10 章）或 "
                "卷末盘点，缺什么做什么。按主文档单元流程：起草 → confirm 拍板"
                "（自动附体检报告）→ 批准后落盘并同步台账 → 停下。"
            )

    # ── 会话与 resume 定位（DEC-020 第 4 条）──
    # resume 必须回到原 trace 所在 agent 的当前会话 thread——Command(resume)
    # 打到无挂起 interrupt 的新 thread 会直接报错。找不到匹配（状态被重建
    # /旧 trace）时按新会话开（回执内容已在产物里，最多重起草一轮）。
    new_session = True
    if resume_trace:
        for name in state.active_threads:
            if state.active_traces.get(name) == resume_trace:
                agent = name
                thread_id = state.active_threads[name]
                new_session = False
                break
    if new_session:
        thread_id = _session_thread(state, base_thread_id, agent, new=True)
    state.phase = agent
    return RouteDecision(
        agent_name=agent, thread_id=thread_id, directive=directive,
        phase=agent, new_session=new_session,
    )


def commit_route(workspace: Path, decision: RouteDecision, trace_id: str) -> None:
    """generate_stream 拿到 trace 后落状态（会话与 active trace 登记）。"""
    state = load_pipeline_state(workspace)
    # active_threads 已在 _session_thread 更新（load 的同一文件需重读合并）；
    # 此处重放派生逻辑保持幂等：按 decision.thread_id 直接登记。
    agent = decision.agent_name or "legacy-design"
    state.active_threads[agent] = decision.thread_id
    state.phase = decision.phase if decision.phase != "legacy" else state.phase
    state.active_traces[agent] = trace_id
    seq = decision.thread_id.rsplit(":", 1)[-1]
    if seq.isdigit():
        state.sessions[agent] = max(state.sessions.get(agent, 0), int(seq))
    save_pipeline_state(workspace, state)


def on_interrupt(workspace: Path, agent_name: str | None, trace_id: str) -> None:
    """interrupt 命中（awaiting_input）：登记 active trace 供 resume 定位与作废。"""
    agent = agent_name or "legacy-design"
    state = load_pipeline_state(workspace)
    state.active_traces[agent] = trace_id
    save_pipeline_state(workspace, state)


def on_run_complete(workspace: Path, agent_name: str | None) -> None:
    """run 正常完成：清 active trace + 回炉段推进 + 相位重扫。"""
    agent = agent_name or "legacy-design"
    state = load_pipeline_state(workspace)
    state.active_traces.pop(agent, None)
    if state.rework:
        if state.rework.stage == REWORK_DESIGN and agent == PHASE_DESIGN:
            state.rework.stage = REWORK_IMPACT
        elif state.rework.stage == REWORK_IMPACT and agent == PHASE_STRUCTURE:
            state.rework = None
    if classify_workspace(workspace) == "pipeline":
        state.phase = scan_phase(workspace)
    save_pipeline_state(workspace, state)


def on_run_abandoned(workspace: Path, agent_name: str | None) -> None:
    """run 被作废（丢弃重开 / 用户停止）：只清 active trace，不动回炉段。"""
    agent = agent_name or "legacy-design"
    state = load_pipeline_state(workspace)
    state.active_traces.pop(agent, None)
    save_pipeline_state(workspace, state)


def pop_awaiting_trace(workspace: Path, agent_name: str | None) -> str | None:
    """丢弃重开：取出并清除该 agent 的未决拍板 trace_id（供执行端作废账面）。"""
    agent = agent_name or "legacy-design"
    state = load_pipeline_state(workspace)
    trace = state.active_traces.pop(agent, None)
    save_pipeline_state(workspace, state)
    return trace


def pipeline_status(workspace: Path) -> dict:
    """桌面端阶段条数据（FR-010）：相位 / 步骤 / 回炉 / 未决拍板。"""
    workspace = Path(workspace)
    state = load_pipeline_state(workspace)
    kind = classify_workspace(workspace)
    fname, label = next_design_step(workspace)
    awaiting = {
        agent: trace for agent, trace in state.active_traces.items() if trace
    }
    return {
        "kind": kind,
        "phase": state.phase,
        "next_unit": label if kind == "pipeline" else "旧版修订模式",
        "design_done": not fname,
        "rework": state.rework.model_dump(mode="json") if state.rework else None,
        "awaiting": awaiting,
        "updated_at": state.updated_at,
    }


__all__ = [
    "DESIGN_FILES",
    "RouteDecision",
    "classify_workspace",
    "commit_route",
    "next_design_step",
    "on_interrupt",
    "on_run_abandoned",
    "on_run_complete",
    "pipeline_status",
    "pop_awaiting_trace",
    "route",
    "scan_phase",
]
