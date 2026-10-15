"""单主控三步管线编排层（REQ-20261015-161200 FR-002/003，DEC-005）。

编排归 executor 代码：管「下一步做什么单元、用哪个会话 thread」。
产物目录即进度真相源（FR-001）：outline/ 目录扫描定进度；`outline/_pipeline.json`
是可重建的加速层（contracts.outline_pipeline 共享契约）。

三步制（DEC-001）：①定世界观 → ②定总纲和卷纲 → ③定章纲批滚动。
流程推进由主控 agent（writer）按目录自主调度；本层只提供下一单元提示与
会话/resume 定位，不再有泳道路由与回炉状态机。

legacy（DEC-011）：旧五件套（storyline.md）与旧七步（outline/00-06 文件）
工作区均只读——agent_name=None 装配首个 main，directive 声明只读模式。

纯同步模块（唯一副作用是状态文件读写），可独立单测。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from contracts.outline_ledger_contract import (
    BATCH_FILE_PATTERN,
    MASTER_FILENAME,
    VOLUME_FILE_PATTERN,
    WORLDVIEW_FILENAME,
)
from contracts.outline_pipeline import (
    PHASE_CHAPTERS,
    PHASE_LEGACY,
    PHASE_OUTLINE,
    PHASE_WORLDVIEW,
    PipelineState,
    load_pipeline_state,
    save_pipeline_state,
)

# 单主控 agent 名（architecture.json 唯一 main）
MAIN_AGENT = "writer"

# 旧七步产物签名前缀（00-赛道 ~ 06-章纲；命中即 legacy 只读）
_SEVEN_STEP_PREFIXES = ("00-", "01-", "02-", "03-", "04-", "05-", "06-")

# legacy 会话键（与管线主控会话键区分，互不串台）
_LEGACY_AGENT_KEY = "legacy"

# 阶段条三步标签（desktop pipeline-steps 同名对齐；next_unit 取值即此三者）
LABEL_WORLDVIEW = "世界观"
LABEL_OUTLINE = "总纲卷纲"
LABEL_CHAPTERS = "章纲"


@dataclass
class RouteDecision:
    agent_name: str | None          # None = manifest 首个 main（legacy 只读模式）
    thread_id: str                  # langgraph thread（会话派生）
    directive: str                  # 任务书（拼进 user prompt 头部）
    phase: str                      # worldview / outline / chapters / legacy
    new_session: bool = False       # 本轮是否新会话（fresh 丢弃重开标记）


@dataclass
class OutlineProgress:
    """outline/ 目录扫描结果（目录即进度）。"""

    worldview_done: bool = False
    master_done: bool = False
    volumes: list[int] = field(default_factory=list)       # 已有卷纲的卷号升序
    batches: dict[int, int] = field(default_factory=dict)  # 卷号 → 最大批号


def classify_workspace(workspace: Path) -> str:
    """"legacy"（旧五件套或旧七步，只读）| "pipeline"（三步制 / 全新）。"""
    workspace = Path(workspace)
    if (workspace / "storyline.md").exists():
        return "legacy"
    outline = workspace / "outline"
    if outline.is_dir():
        for f in outline.iterdir():
            if f.name.startswith(_SEVEN_STEP_PREFIXES):
                return "legacy"
    return "pipeline"


def scan_progress(workspace: Path) -> OutlineProgress:
    """扫描 outline/ 目录推导进度（快照函数，不落盘）。"""
    workspace = Path(workspace)
    prog = OutlineProgress(
        worldview_done=(workspace / "outline" / WORLDVIEW_FILENAME).is_file(),
        master_done=(workspace / "outline" / MASTER_FILENAME).is_file(),
    )
    if not (workspace / "outline").is_dir():
        return prog
    for f in (workspace / "outline").iterdir():
        m = VOLUME_FILE_PATTERN.match(f.name)
        if m:
            prog.volumes.append(int(m.group(1)))
            continue
        m = BATCH_FILE_PATTERN.match(f.name)
        if m:
            vol, batch = int(m.group(1)), int(m.group(2))
            prog.batches[vol] = max(prog.batches.get(vol, 0), batch)
    prog.volumes.sort()
    return prog


def scan_phase(workspace: Path) -> str:
    """按目录定下一步的段位：世界观 → 总纲卷纲 → 章纲（章纲批滚动归章纲段）。"""
    prog = scan_progress(workspace)
    if not prog.worldview_done:
        return PHASE_WORLDVIEW
    if not prog.master_done or not prog.volumes:
        return PHASE_OUTLINE
    return PHASE_CHAPTERS


def next_unit_label(workspace: Path) -> str:
    """下一单元标签（阶段条高亮与任务书共用；取值为三步标签之一）。"""
    phase = scan_phase(workspace)
    return {
        PHASE_WORLDVIEW: LABEL_WORLDVIEW,
        PHASE_OUTLINE: LABEL_OUTLINE,
        PHASE_CHAPTERS: LABEL_CHAPTERS,
    }[phase]


def _unit_detail(workspace: Path) -> str:
    """下一单元的具体说明（任务书正文用）。"""
    prog = scan_progress(workspace)
    if not prog.worldview_done:
        return "本单元：①定世界观（outline/世界观.md）"
    if not prog.master_done:
        return "本单元：②定总纲（outline/总纲.md + character/core/ 主角与 BOSS 档案）"
    if not prog.volumes:
        return "本单元：②定第 1 卷卷纲（outline/卷纲-第1卷.md，含黄金三章设计）"
    cur = prog.volumes[-1]
    nxt = prog.batches.get(cur, 0) + 1
    return (
        f"本单元：③第 {cur} 卷第 {nxt} 批章纲（outline/章纲-卷{cur}批{nxt}.md，"
        "每批 5-8 章，同步更新期待台账）。若当前卷的期待已清账、卷末大兑现已安排，"
        f"改做第 {cur + 1} 卷卷纲"
    )


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

    # ── legacy：旧五件套 / 旧七步 → 只读模式（DEC-011）──
    if classify_workspace(workspace) == "legacy":
        thread_id = _session_thread(state, base_thread_id, _LEGACY_AGENT_KEY, new=True)
        return RouteDecision(
            agent_name=None,
            thread_id=thread_id,
            phase=PHASE_LEGACY,
            directive=(
                "【旧版工作区·只读】本 workspace 是旧版结构（五件套或旧七步 outline），"
                "不支持在新流程续写修订。可以查看、总结、答疑；"
                "不修改任何文件。用户要开新书时新建工作目录。"
            ),
            new_session=True,
        )

    agent = MAIN_AGENT
    directive = (
        f"【管线任务】当前下一单元：{next_unit_label(workspace)}。{_unit_detail(workspace)}。"
        "按主文档单元流程：委托子代理起草 → confirm_with_user 拍板 → 批准后委托落盘 → 停下。"
        "用户要求修改既有产物时，按「谁产出谁能改」委托原产出子代理修订，修订稿同样过拍板。"
    )

    # ── 会话与 resume 定位（沿用：resume 回到原会话 thread）──
    # Command(resume) 打到无挂起 interrupt 的新 thread 会直接报错。找不到匹配
    # （状态被重建/旧 trace）时按新会话开（回执内容已在产物里，最多重起草一轮）。
    new_session = True
    if resume_trace:
        for name in state.active_threads:
            if state.active_traces.get(name) == resume_trace:
                agent = name if name != _LEGACY_AGENT_KEY else MAIN_AGENT
                thread_id = state.active_threads[name]
                new_session = False
                break
    if new_session:
        thread_id = _session_thread(state, base_thread_id, agent, new=True)
    state.phase = scan_phase(workspace)
    return RouteDecision(
        agent_name=agent, thread_id=thread_id, directive=directive,
        phase=scan_phase(workspace), new_session=new_session,
    )


def commit_route(workspace: Path, decision: RouteDecision, trace_id: str) -> None:
    """generate_stream 拿到 trace 后落状态（会话与 active trace 登记）。"""
    state = load_pipeline_state(workspace)
    agent = decision.agent_name or _LEGACY_AGENT_KEY
    state.active_threads[agent] = decision.thread_id
    if decision.phase != PHASE_LEGACY:
        state.phase = decision.phase
    state.active_traces[agent] = trace_id
    seq = decision.thread_id.rsplit(":", 1)[-1]
    if seq.isdigit():
        state.sessions[agent] = max(state.sessions.get(agent, 0), int(seq))
    save_pipeline_state(workspace, state)


def on_interrupt(workspace: Path, agent_name: str | None, trace_id: str) -> None:
    """interrupt 命中（awaiting_input）：登记 active trace 供 resume 定位与作废。"""
    agent = agent_name or _LEGACY_AGENT_KEY
    state = load_pipeline_state(workspace)
    state.active_traces[agent] = trace_id
    save_pipeline_state(workspace, state)


def on_run_complete(workspace: Path, agent_name: str | None) -> None:
    """run 正常完成：清 active trace + 相位重扫。"""
    agent = agent_name or _LEGACY_AGENT_KEY
    state = load_pipeline_state(workspace)
    state.active_traces.pop(agent, None)
    if classify_workspace(workspace) == "pipeline":
        state.phase = scan_phase(workspace)
    save_pipeline_state(workspace, state)


def on_run_abandoned(workspace: Path, agent_name: str | None) -> None:
    """run 被作废（丢弃重开 / 用户停止）：只清 active trace。"""
    agent = agent_name or _LEGACY_AGENT_KEY
    state = load_pipeline_state(workspace)
    state.active_traces.pop(agent, None)
    save_pipeline_state(workspace, state)


def pop_awaiting_trace(workspace: Path, agent_name: str | None) -> str | None:
    """丢弃重开：取出并清除该 agent 的未决拍板 trace_id（供执行端作废账面）。"""
    agent = agent_name or _LEGACY_AGENT_KEY
    state = load_pipeline_state(workspace)
    trace = state.active_traces.pop(agent, None)
    save_pipeline_state(workspace, state)
    return trace


def pipeline_status(workspace: Path) -> dict:
    """桌面端阶段条数据（FR-008）：kind / 相位 / 下一单元标签 / 未决拍板。"""
    workspace = Path(workspace)
    state = load_pipeline_state(workspace)
    kind = classify_workspace(workspace)
    return {
        "kind": kind,
        "phase": state.phase,
        "next_unit": next_unit_label(workspace) if kind == "pipeline" else "旧版工作区（只读）",
        "awaiting": {
            agent: trace for agent, trace in state.active_traces.items() if trace
        },
        "updated_at": state.updated_at,
    }


__all__ = [
    "MAIN_AGENT",
    "OutlineProgress",
    "RouteDecision",
    "classify_workspace",
    "commit_route",
    "next_unit_label",
    "on_interrupt",
    "on_run_abandoned",
    "on_run_complete",
    "pipeline_status",
    "pop_awaiting_trace",
    "route",
    "scan_phase",
    "scan_progress",
]
