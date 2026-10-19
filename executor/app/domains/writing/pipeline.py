"""五段式管线编排层（REQ-20261018-115508 FR-002，DEC-001/002/010）。

编排归 executor 代码：管「当前在哪一大段、装配哪份段规程、用哪个会话
thread」。产物目录即进度真相源：五段产物树扫描定进度（contracts.
writing_artifacts 唯一实现）；`.writing/state.json` 是可重建的加速层。

五段制（DEC-001 单 agent 技能切换）：题材 → 总纲 → 卷纲 → 章纲 → 正文。
路由三来源优先级：resume 续跑（回到原会话）> 点名跳段（handoff 写入的
override_stage，消费即清）> 目录扫描自动判定（DEC-002）。

legacy（DEC-010）：旧五件套（storyline.md）、旧七步（outline/00-06）、
三步制（outline/世界观.md 等）一律只读——agent_name=None 装配首个 main，
directive 声明只读模式；classify 委托 contracts（旧三步制 outline/ 目录
存在即 legacy）。

纯同步模块（唯一副作用是状态文件读写），可独立单测。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from contracts.writing_artifacts import (
    STAGE_CHAPTER,
    STAGE_LABELS,
    STAGE_LEGACY,
    STAGE_THEME,
    MASTER_FILENAME,
    STYLE_FILENAME,
    THEME_FILENAME,
    WritingProgress,
    classify_writing_workspace,
    derive_stage,
    load_writing_state,
    save_writing_state,
    scan_writing_progress,
)

# 单 agent 架构名（architecture.json 唯一 main）
MAIN_AGENT = "author"

# legacy 会话键（与主管线会话键区分，互不串台）
_LEGACY_AGENT_KEY = "legacy"

# 注入禁令（2026-10-19 线上事故）：directive 只许携带运行时事实（当前段、
# 进度摘要、下一单元目标），不拼任务说明或行为纪律——那些只住发版的段规程
# 提示词。曾在 _STAGE_DETAIL 里硬编码各段任务书摘要 + 「一轮最多问 1-2 个
# 问题」纪律句，发版改了提示词后这些代码副本仍每轮注入，已删（见
# agent._build_user_prompt 的注入禁令注释）。


@dataclass
class RouteDecision:
    agent_name: str | None          # None = manifest 首个 main（legacy 只读模式）
    thread_id: str                  # langgraph thread（会话派生）
    directive: str                  # 运行时事实（当前段+进度，拼进 user prompt；禁止行为指令）
    phase: str                      # theme/outline/volume/chapter/text/legacy
    new_session: bool = False       # 本轮是否新开会话线程（首条消息或丢弃重开）
    used_override: bool = False     # 本轮是否消费了点名跳段（commit 时清除）
    conversation_id: str = ""       # 客户端会话 id（base thread_id，粘性路由与日志的键）
    resume_degraded: bool = False   # resume trace 已失配：降级为普通消息续跑


def classify_workspace(workspace: Path) -> str:
    """"legacy"（旧五件套/旧七步/三步制，只读）| "five"（五段制/全新）。"""
    return classify_writing_workspace(workspace)


def scan_progress(workspace: Path) -> WritingProgress:
    """五段产物树扫描（快照函数，不落盘；委托 contracts 唯一实现）。"""
    return scan_writing_progress(workspace)


def scan_stage(workspace: Path) -> str:
    """按产物树定下一大段（DEC-002 目录扫描自动判定）。"""
    return derive_stage(scan_writing_progress(workspace))


def current_stage(workspace: Path) -> str:
    """装配用段解析（C3 修复）：点名跳段（override_stage）优先于扫描——
    与 route() 同口径，供 agent.py 段规程注入消费（stage_gate 自行读状态，
    同一语义）。legacy 返回扫描段（legacy 装配走只读 directive）。"""
    if classify_workspace(workspace) == "legacy":
        return scan_stage(workspace)
    override = load_writing_state(workspace).override_stage
    return override if override in STAGE_LABELS else scan_stage(workspace)


def _status_line(workspace: Path, rel: str) -> str:
    """读产物文件头的状态行（进度摘要用；缺文件返回未落盘标记）。"""
    try:
        for ln in (workspace / rel).read_text(encoding="utf-8").splitlines()[:6]:
            s = ln.strip()
            if s.startswith(">") and "状态" in s:
                return s.lstrip("> ").strip()
    except (OSError, UnicodeDecodeError):
        pass
    return "未落盘"


def _progress_summary(workspace: Path, prog: WritingProgress) -> str:
    """进度摘要（每轮注入 directive，FR-002：文件事实+状态行，几百字内）。"""
    lines = [
        f"主题：{_status_line(workspace, THEME_FILENAME)}",
        f"总纲：{_status_line(workspace, MASTER_FILENAME)}",
        f"文风卡：{'已就绪' if prog.style_ready else '待起草（章纲段首次进入时起草）'}",
        f"卷纲：已到第 {prog.volumes[-1]} 卷" if prog.volumes else "卷纲：未开始",
        f"章纲：已出 {len(prog.chapter_outlines)} 章"
        + (f"（最新第 {prog.chapter_outlines[-1]} 章）" if prog.chapter_outlines else ""),
    ]
    if prog.chapter_texts:
        finalized = sum(1 for m in prog.chapter_texts.values() if m.finalized)
        lines.append(
            f"正文：已写 {len(prog.chapter_texts)} 章（{finalized} 章已人修定稿，"
            f"最新第 {max(prog.chapter_texts)} 章）"
        )
    else:
        lines.append("正文：未开始")
    return "\n".join(lines)


def _unit_detail(workspace: Path, stage: str) -> str:
    """当前段附带的运行时事实（下一卷/章目标、细纲库存）。

    只报磁盘事实，不带任务说明与提醒阈值——那些住发版的段规程提示词。
    """
    prog = scan_writing_progress(workspace)
    if stage == "volume":
        nxt = (prog.volumes[-1] if prog.volumes else 0) + 1
        return f"当前目标：第 {nxt} 卷卷纲。"
    if stage == STAGE_CHAPTER:
        stock = len(prog.chapter_outlines) - len(prog.chapter_texts)
        return f"细纲库存 {stock} 章。"
    if stage == "text":
        outlined = set(prog.chapter_outlines)
        written = set(prog.chapter_texts)
        nxt = min(outlined - written) if outlined - written else None
        return f"当前目标：第 {nxt} 章正文。" if nxt else ""
    return ""


def next_unit_label(workspace: Path) -> str:
    """下一单元标签（阶段条高亮与任务书共用；五段标签之一或 legacy 文案）。"""
    if classify_workspace(workspace) == "legacy":
        return "旧版工作区（只读）"
    return STAGE_LABELS.get(scan_stage(workspace), STAGE_LABELS[STAGE_THEME])


def _sticky_thread_id(base_thread_id: str, agent: str, fork_number: int) -> str:
    """粘性线程名：派生号即丢弃重开计数（首会话=1）。"""
    return f"{base_thread_id}:{agent}:{fork_number}"


def _conversation_thread(
    state, base_thread_id: str, agent: str, *, fork: bool = False
) -> tuple[str, int]:
    """会话粘性线程（REQ-20261019-222403 FR-001/DEC-001）。

    同一客户端会话（base_thread_id）全程复用同一 langgraph 线程，历史跨轮
    累积；仅丢弃重开（fresh_session）递增派生号开新线程。返回
    (thread_id, fork_number)。旧 seq 制线程不迁移（DEC-010）：老书首次对话
    按派生号 1 起新线程，断尾由执行端 normalize 清理。
    """
    if fork:
        fork_number = state.conversation_forks.get(base_thread_id, 0) + 1
    else:
        fork_number = state.conversation_forks.get(base_thread_id, 1)
    return _sticky_thread_id(base_thread_id, agent, fork_number), fork_number


def route(
    workspace: Path,
    base_thread_id: str,
    *,
    user_text: str = "",
    resume_trace: str | None = None,
    fresh_session: bool = False,
) -> RouteDecision:
    """路由决策（唯一入口）。不写盘——状态落盘由 commit_route 调用方驱动。

    Args:
        resume_trace: resume 续跑的 trace_id（有值 = 回到当前会话，不消费跳段）。
        fresh_session: 丢弃重开（作废旧 trace，递增派生号开新线程）。
    """
    workspace = Path(workspace)
    state = load_writing_state(workspace)

    # ── legacy：旧五件套 / 旧七步 / 三步制 → 只读模式（DEC-010）──
    if classify_workspace(workspace) == "legacy":
        thread_id, fork_number = _conversation_thread(
            state, base_thread_id, _LEGACY_AGENT_KEY, fork=fresh_session
        )
        return RouteDecision(
            agent_name=None,
            thread_id=thread_id,
            phase=STAGE_LEGACY,
            # 只读护栏是注入禁令的唯一例外：不是创作行为指令，是防止 agent
            # 往旧结构工作区写新格式产物、毁掉旧作数据的安全边界。
            directive=(
                "【旧版工作区·只读】本 workspace 是旧版结构（五件套、旧七步或三步制），"
                "不支持在新流程续写修订。可以查看、总结、答疑；"
                "不修改任何文件。用户要开新书时新建工作目录。"
            ),
            new_session=fresh_session or base_thread_id not in state.conversation_forks,
            conversation_id=base_thread_id,
        )

    agent = MAIN_AGENT
    # ── 阶段三来源：resume > 点名跳段（消费即清，commit 时落）> 目录扫描 ──
    used_override = False
    if resume_trace:
        stage = scan_stage(workspace)
    elif state.override_stage:
        stage = state.override_stage
        used_override = True
    else:
        stage = scan_stage(workspace)

    label = STAGE_LABELS.get(stage, stage)
    prog = scan_writing_progress(workspace)
    directive = (
        f"【五段管线】当前段：{label}（{stage}）。{_unit_detail(workspace, stage)}\n"
        f"【进度摘要】\n{_progress_summary(workspace, prog)}"
    )

    # ── 会话定位（REQ-20261019-222403 FR-001 粘性）：resume 沿未决拍板回原
    # 会话；普通消息复用当前会话线程；丢弃重开递增派生号。trace 失配的
    # resume 降级为普通消息（原拍板已不在，答案文本按新输入续跑）。──
    resume_degraded = False
    matched_agent: str | None = None
    if resume_trace:
        for name in state.active_threads:
            if state.active_traces.get(name) == resume_trace:
                matched_agent = name if name != _LEGACY_AGENT_KEY else MAIN_AGENT
                break
        if matched_agent is None:
            resume_degraded = True
    if matched_agent is not None:
        agent = matched_agent
        # 未决拍板所在线程是事实源（commit_route 维护）；粘性派生仅兜底。
        thread_id = state.active_threads.get(matched_agent) or _sticky_thread_id(
            base_thread_id, agent, state.conversation_forks.get(base_thread_id, 1)
        )
        new_session = False
    else:
        agent = MAIN_AGENT
        thread_id, fork_number = _conversation_thread(
            state, base_thread_id, agent, fork=fresh_session
        )
        new_session = fresh_session or (
            fork_number == 1 and base_thread_id not in state.conversation_forks
        )
    return RouteDecision(
        agent_name=agent, thread_id=thread_id, directive=directive,
        phase=stage, new_session=new_session, used_override=used_override,
        conversation_id=base_thread_id, resume_degraded=resume_degraded,
    )


def commit_route(workspace: Path, decision: RouteDecision, trace_id: str) -> None:
    """generate_stream 拿到 trace 后落状态（会话与 active trace 登记；消费跳段）。"""
    state = load_writing_state(workspace)
    agent = decision.agent_name or _LEGACY_AGENT_KEY
    state.active_threads[agent] = decision.thread_id
    if decision.phase != STAGE_LEGACY:
        state.stage = decision.phase
    state.active_traces[agent] = trace_id
    if decision.used_override:
        state.override_stage = None  # 点名跳段消费即清（DEC-002）
    # 粘性派生号回写（FR-001）：从线程名尾段解析，只增不减。
    fork_tail = decision.thread_id.rsplit(":", 1)[-1]
    if decision.conversation_id and fork_tail.isdigit():
        state.conversation_forks[decision.conversation_id] = max(
            state.conversation_forks.get(decision.conversation_id, 0), int(fork_tail)
        )
    save_writing_state(workspace, state)


def on_interrupt(workspace: Path, agent_name: str | None, trace_id: str) -> None:
    """interrupt 命中（awaiting_input）：登记 active trace 供 resume 定位与作废。"""
    agent = agent_name or _LEGACY_AGENT_KEY
    state = load_writing_state(workspace)
    state.active_traces[agent] = trace_id
    save_writing_state(workspace, state)


def on_run_complete(workspace: Path, agent_name: str | None) -> None:
    """run 正常完成：清 active trace + 相位重扫。"""
    agent = agent_name or _LEGACY_AGENT_KEY
    state = load_writing_state(workspace)
    state.active_traces.pop(agent, None)
    if classify_workspace(workspace) == "five":
        state.stage = scan_stage(workspace)
    save_writing_state(workspace, state)


def on_run_abandoned(workspace: Path, agent_name: str | None) -> None:
    """run 被作废（丢弃重开 / 用户停止）：只清 active trace。"""
    agent = agent_name or _LEGACY_AGENT_KEY
    state = load_writing_state(workspace)
    state.active_traces.pop(agent, None)
    save_writing_state(workspace, state)


def pop_awaiting_trace(workspace: Path, agent_name: str | None) -> str | None:
    """丢弃重开：取出并清除该 agent 的未决拍板 trace_id（供执行端作废账面）。"""
    agent = agent_name or _LEGACY_AGENT_KEY
    state = load_writing_state(workspace)
    trace = state.active_traces.pop(agent, None)
    save_writing_state(workspace, state)
    return trace


def pipeline_status(workspace: Path) -> dict:
    """桌面端阶段条数据：kind / 相位 / 下一单元标签 / 未决拍板。"""
    workspace = Path(workspace)
    state = load_writing_state(workspace)
    kind = classify_workspace(workspace)
    # phase 活推导（REQ-20261019-183150）：state.stage 只是上次运行的快照，
    # 部署修复/定稿规则变更后 stale 快照不得压过目录事实继续显示旧段
    # （AC-007：定稿闸上线后卡在卷纲的书打开即回显总纲）。
    phase = current_stage(workspace) if kind == "five" else state.stage
    return {
        "kind": kind,
        "phase": phase,
        "next_unit": next_unit_label(workspace),
        "awaiting": {
            agent: trace for agent, trace in state.active_traces.items() if trace
        },
        "updated_at": state.updated_at,
    }


__all__ = [
    "MAIN_AGENT",
    "RouteDecision",
    "WritingProgress",
    "classify_workspace",
    "commit_route",
    "current_stage",
    "next_unit_label",
    "on_interrupt",
    "on_run_abandoned",
    "on_run_complete",
    "pipeline_status",
    "pop_awaiting_trace",
    "route",
    "scan_progress",
    "scan_stage",
]
