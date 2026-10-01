import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
  finalizeEvolve,
  getEvolveAgent,
  getEvolveMessages,
  getEvolvePoints,
  getEvolveSession,
  getEvolveSessionEventsSince,
  getEvolveSessions,
  getLandingChannel,
  listBenchmarkBatches,
  listEvolveAgents,
  sendEvolveMessage,
  startEvolveAgentSession,
  stopEvolve,
  type BenchmarkBatchSummary,
  type EvolveAgent,
  type EvolveMessage,
  type EvolvePoint,
  type EvolveSession,
} from "@/lib/api";
import AgentLaunchCard from "./AgentLaunchCard";
import ConversationPanel from "./ConversationPanel";
import NewSessionDialog from "./NewSessionDialog";
import PointsDrawer from "./PointsDrawer";

/**
 * 进化工作台 Tab（决策 F/N/C，2026-07-20 重构为两栏）。
 *
 * 两栏布局：
 *   中：对话区（ConversationPanel）—— Agent 选择/启动入口 / 对话流 / 输入框
 *   右：进化点浮窗（PointsDrawer）—— 实时状态 + 拍板按钮
 * （原左侧历史会话已移到独立「进化历史」Tab，本组件不再维护 sessions 列表）
 *
 * Agent 绑定模式（REQ-20261001-131018 DEC-002/003）：
 *   - 启动入口两段式：先选/建进化 Agent（绑作品），再开会话；自由启动退役
 *   - selectedAgentId 持久化 localStorage（跨刷新记住常用 Agent）
 *   - 落地通道占用横幅（FR-003：聊天并行、落地排队——占用时提示，可照常对话）
 *
 * 跨 tab 选中联动（DD4）：
 *   - initialSessionId：URL ?session=xxx 解析出的 id（EvolvePage 透传）
 *   - initialSession：HistoryTab 点选时透传的完整 session 对象（含 status，免重复拉详情）
 *   - useEffect([initialSessionId])：id 变化时自动选中（有 initialSession 直接用，否则按 id 拉详情）
 *
 * 数据流：
 *   - 选 Agent → 开会话 → 订阅 Pull 事件流 → 拉取 messages + points
 *   - 用户发消息 → POST /messages → Pull 推 Agent 回复（持久化 + 增量拉取）
 *   - 进化点状态变更 → proposal 事件 → 刷新 points
 *   - 用户拍板 → POST /finalize → finalizing → 完成后自动跳 review-report（决策 AA）
 *
 * 双向高亮联动（决策 N）：
 *   - 浮窗点击进化点 → highlightedPointId（滚动对话到该点讨论位置）
 *   - 对话区 hover/点击卡片 → 同一 state 反向高亮浮窗
 */
const AGENT_STORAGE_KEY = "evolve.selectedAgentId";

export default function WorkbenchTab({
  initialSessionId,
  initialSession,
}: {
  initialSessionId: string | null;
  initialSession: EvolveSession | null;
}) {
  const navigate = useNavigate();

  // 可附带的评测批次列表（轮询，进化启动入口用；只列已完成批次——运行中无弱点报告）
  const [batches, setBatches] = useState<BenchmarkBatchSummary[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [selectedStatus, setSelectedStatus] = useState<string | null>(null);

  // 进化 Agent（REQ-20261001-131018）
  const [agents, setAgents] = useState<EvolveAgent[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(() =>
    localStorage.getItem(AGENT_STORAGE_KEY),
  );
  const [landingOccupied, setLandingOccupied] = useState(false);
  // 「新开会话」对话框（独立组件，交互修正 2026-10-01：不依赖工作台空闲）
  const [newSessionOpen, setNewSessionOpen] = useState(false);
  const [hasAnySession, setHasAnySession] = useState(false);

  // 对话 + 进化点
  const [messages, setMessages] = useState<EvolveMessage[]>([]);
  const [points, setPoints] = useState<EvolvePoint[]>([]);
  const [acceptedCount, setAcceptedCount] = useState(0);

  // 交互态
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const [highlightedPointId, setHighlightedPointId] = useState<string | null>(null);
  const streamCancelRef = useRef<(() => void) | null>(null);

  // ── 运行中活动指示（FR-005）────────────────────────────
  // 后端从 llm_start/tool_start span 事件派生 activity 帧；这里只保留最新
  // 一条（label + 时间戳）。90s 无新帧视为空闲自动隐藏——覆盖取证段频繁工具
  // 调用与思考段长回复生成，round 结束后指示自然消退。
  const [activity, setActivity] = useState<{ label: string; at: number } | null>(null);
  useEffect(() => {
    if (!activity) return;
    const left = 90_000 - (Date.now() - activity.at);
    const timer = setTimeout(() => setActivity(null), Math.max(left, 0));
    return () => clearTimeout(timer);
  }, [activity]);

  // ── 轮询：评测批次 + Agent 列表 + 落地通道 ─────────────────
  // 只列已完成（done/partial）批次——弱点视图只对完成批次有意义
  const refreshBatches = useCallback(async () => {
    const resp = await listBenchmarkBatches(20).catch(() => null);
    if (resp) {
      setBatches(resp.batches.filter((b) => b.status === "done" || b.status === "partial"));
    }
  }, []);

  const refreshAgents = useCallback(async () => {
    const resp = await listEvolveAgents().catch(() => null);
    if (resp) setAgents(resp.agents);
  }, []);

  const refreshLandingChannel = useCallback(async () => {
    const resp = await getLandingChannel().catch(() => null);
    if (resp) setLandingOccupied(resp.occupied);
  }, []);

  const refreshHasAnySession = useCallback(async () => {
    const resp = await getEvolveSessions(1).catch(() => null);
    if (resp) setHasAnySession(resp.total > 0);
  }, []);

  useEffect(() => {
    void refreshBatches();
    void refreshAgents();
    void refreshLandingChannel();
    void refreshHasAnySession();
    const timer = setInterval(() => {
      void refreshBatches();
      void refreshAgents();
      void refreshLandingChannel();
      void refreshHasAnySession();
    }, 10000);
    return () => {
      clearInterval(timer);
      streamCancelRef.current?.();
    };
  }, [refreshBatches, refreshAgents, refreshLandingChannel, refreshHasAnySession]);

  const handleSelectAgent = useCallback((agentId: string | null) => {
    setSelectedAgentId(agentId);
    if (agentId) localStorage.setItem(AGENT_STORAGE_KEY, agentId);
    else localStorage.removeItem(AGENT_STORAGE_KEY);
  }, []);

  // ── 拉取会话详情（messages + points）────────────────────────
  // 拉取进化点（独立于消息——proposal 事件时只刷进化点，避免覆盖流式 token）
  const loadPoints = useCallback(async (sessionId: string) => {
    try {
      const ptsResp = await getEvolvePoints(sessionId);
      if (ptsResp) {
        setPoints(ptsResp.points);
        setAcceptedCount(ptsResp.accepted_count);
      }
    } catch {
      setPoints([]);
      setAcceptedCount(0);
    }
  }, []);

  // 拉取消息（只在 phase 切换/选会话/SSE end 时调用——避免覆盖流式 token）
  const loadMessages = useCallback(async (sessionId: string) => {
    try {
      const msgResp = await getEvolveMessages(sessionId);
      if (msgResp) setMessages(msgResp.messages);
    } catch {
      setMessages([]);
    }
  }, []);

  const loadSessionDetail = useCallback(async (sessionId: string) => {
    // 选会话时同时拉消息 + 进化点（不涉及流式，安全）
    await Promise.all([loadMessages(sessionId), loadPoints(sessionId)]);
  }, [loadMessages, loadPoints]);

  // ── 选中会话（核心动作，供 initialSessionId 联动 + handleStart 复用）──
  // 依赖只列 loadSessionDetail——subscribeStream 是 hoisted 函数声明，每次 render
  // 重建，若列进依赖会让 selectSession 每次 render 都变，破坏 useEffect 幂等性。
  // subscribeStream 内部用 setState 函数式更新 + ref，闭包稳定性已足够。
  const selectSession = useCallback(
    (s: EvolveSession) => {
      streamCancelRef.current?.();
      setSelectedSessionId(s.session_id);
      setSelectedStatus(s.status);
      setHighlightedPointId(null);
      setActivity(null);
      void loadSessionDetail(s.session_id);
      // 活跃会话订阅 Pull 事件流
      if (["running", "conversing", "finalizing"].includes(s.status)) {
        subscribeStream(s.session_id);
      }
    },
    [loadSessionDetail],
  );

  // ── URL ?session=xxx 联动（DD4）─────────────────────────────
  // HistoryTab 点选 → EvolvePage 写 URL → 本 effect 触发选中。
  // 有 initialSession 对象直接用（免拉详情）；只有 id（刷新场景）时按 id 拉详情。
  useEffect(() => {
    if (!initialSessionId) return;
    // 已选中相同 session 则跳过（幂等，避免重复订阅）
    if (initialSessionId === selectedSessionId) return;

    if (initialSession && initialSession.session_id === initialSessionId) {
      selectSession(initialSession);
    } else {
      // 刷新场景：URL 有 id 但无 session 对象 → 拉详情后选中
      void getEvolveSession(initialSessionId)
        .then((sess) => selectSession(sess))
        .catch(() => {
          // session 不存在或拉取失败：静默，交给自动定位最新会话的 effect
        });
    }
  }, [initialSessionId, initialSession, selectSession, selectedSessionId]);

  // ── 自动定位最新会话（交互修正 2026-10-01）──────────────────
  // 工作台常驻显示「当前会话」：无 URL 指定且尚未选中时，自动定位——
  //   记住了 Agent → 该 Agent 最新会话；否则 → 全局最新会话。
  // 用户从历史 Tab 选了旧会话（URL 联动）后不再自动跳（尊重显式选择）。
  // 仅在「从未选中」时执行一次（autoFocusedRef 防轮询重复触发）。
  const autoFocusedRef = useRef(false);
  useEffect(() => {
    if (initialSessionId || selectedSessionId || autoFocusedRef.current) return;
    autoFocusedRef.current = true;
    (async () => {
      try {
        if (selectedAgentId) {
          const detail = await getEvolveAgent(selectedAgentId);
          const latest = detail.sessions?.[0];
          if (latest) {
            selectSession(latest);
            return;
          }
          // 该 Agent 名下无会话：保持空态（引导开新会话）
          return;
        }
        const resp = await getEvolveSessions(1);
        if (resp.sessions[0]) selectSession(resp.sessions[0]);
      } catch {
        // 拉取失败：保持空态，不阻断
      }
    })();
  }, [initialSessionId, selectedSessionId, selectedAgentId, selectSession]);

  // ── 在 Agent 下开新会话（DEC-002 入口统一绑作品；对话框发起）──
  // benchmarkBatchId 可选：附带时 Agent 把评测弱点视图作为补充证据
  async function handleStart(agentId: string, benchmarkBatchId: string | null) {
    setStarting(true);
    setMessages([]);
    setPoints([]);
    setAcceptedCount(0);
    try {
      const resp = await startEvolveAgentSession(agentId, benchmarkBatchId);
      setNewSessionOpen(false);
      setSelectedSessionId(resp.session_id);
      setSelectedStatus("running");
      setActivity(null); // 新会话不继承旧会话的活动指示（FR-005）
      toast.success(`进化已启动：${resp.session_id.slice(0, 8)}`);
      subscribeStream(resp.session_id);
      void refreshBatches();
      void refreshHasAnySession();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "启动进化失败");
    } finally {
      setStarting(false);
    }
  }

  // ── trace 重构：Pull 轮询（设计 20260720_154825）──
  // 重构后事件密度大幅降低（不再有 token 流），轮询间隔放宽到 2s。
  // 前端不再维护临时消息 state——所有消息（assistant/tool/system）都从
  // evolve_messages 权威存储拉取，事件帧只是"通知该刷消息了"的信号。
  async function subscribeStream(sessionId: string) {
    streamCancelRef.current?.();
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let sinceSeq = 0;

    streamCancelRef.current = () => {
      cancelled = true;
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    };

    const POLL_INTERVAL_MS = 2000;  // 重构后无 token 流，2s 足够
    const ERROR_BACKOFF_MS = 3000;

    const poll = async () => {
      if (cancelled) return;
      try {
        const resp = await getEvolveSessionEventsSince(sessionId, sinceSeq);
        if (cancelled) return;

        // 事件驱动刷新：只要 trace 有新事件（max_seq 推进），就刷新消息 + 进化点。
        // 不依赖后端产出特定的 message_updated 帧——max_seq 推进说明 Agent 在跑
        // （写 llm/tool/business 事件），此时拉一次权威存储即可拿到最新消息。
        // 注：业务事件的 input 被 recorder 外化后 event.input 为 null，导致
        // _trace_event_to_sse 派生出的 frames 可能为空，故以 max_seq 推进为准，
        // 不以 frames.length 为准（否则永远不刷新）。
        // sinceSeq 游标保证不重复处理事件；loadMessages 全量替换 state，天然幂等。
        const has_new_events = resp.max_seq > sinceSeq;
        if (has_new_events) {
          void loadMessages(sessionId);
          void loadPoints(sessionId);
        }

        // 派发每帧到 handleSseFrame（处理 phase/proposal/end/error 等副作用）
        for (const frame of resp.frames) {
          handleSseFrame(sessionId, frame);
        }
        sinceSeq = resp.max_seq;

        // 用后端权威 session_status 纠正本地 selectedStatus（防 phase 帧丢失导致
        // 状态漂移——漂移到非 conversing 会让输入框 + 拍板按钮误禁用）。
        // 函数式更新避免闭包里的 selectedStatus 陈旧。参考 evaluation.tsx 范式。
        if (resp.session_status) {
          setSelectedStatus((prev) =>
            prev !== resp.session_status ? resp.session_status : prev,
          );
        }

        // has_more=true：事件积压（罕见，重构后事件密度低），立即续拉。
        if (resp.has_more) {
          timer = setTimeout(poll, 0);
          return;
        }

        // session_status 终态：派发 end 帧，然后停止轮询。
        // cancel_timeout 与后端 is_terminal 对齐（review finding-13）——RC3 修复后
        // 该状态首次真正可达，遗漏会让 2s 轮询永不停。
        const terminal = [
          "published",
          "discarded",
          "failed",
          "cancelled",
          "cancel_timeout",
        ].includes(resp.session_status);
        if (terminal) {
          handleSseFrame(sessionId, { type: "end" });
          return;
        }

        // running 中：安排下次轮询。
        timer = setTimeout(poll, POLL_INTERVAL_MS);
      } catch {
        if (cancelled) return;
        // 网络抖动：退避后继续（不丢已拉帧）。
        timer = setTimeout(poll, ERROR_BACKOFF_MS);
      }
    };

    poll();
  }

  function handleSseFrame(sessionId: string, frame: any) {
    if (!frame || typeof frame !== "object") return;
    switch (frame.type) {
      case "heartbeat":
        break;
      case "activity": {
        // FR-005 运行中活动信号：llm_start/tool_start span 派生。
        // at 用帧携带的事件侧 ts（review finding-4）：重放历史帧时按真实发生
        // 时间过期，空闲会话不再出现假「正在运行」指示；解析失败回退本地时钟。
        if (frame.label) {
          const ts = typeof frame.ts === "string" ? Date.parse(frame.ts) : NaN;
          setActivity({
            label: String(frame.label),
            at: Number.isFinite(ts) ? ts : Date.now(),
          });
        }
        break;
      }
      case "message_updated":
        // 消息刷新已由 poll 的事件驱动统一处理（拉到任意新帧即 loadMessages）。
        // 此 case 仅作语义标记，无额外副作用。
        break;
      case "phase":
        // 阶段切换（inspect → conversing → finalizing）——立即纠正本地状态。
        // 消息/进化点刷新已由 poll 统一处理。
        setSelectedStatus(frame.phase);
        setActivity(null);
        break;
      case "proposal":
        // 进化点状态变更——浮窗刷新已由 poll 的 loadPoints 统一处理。
        break;
      case "finalizing":
        // 落地进度事件——消息刷新已由 poll 统一处理。
        break;
      case "log": {
        // 思考日志事件——后端 emit_log 写的 run_meta，未落消息表。
        // 这里不展示（避免与持久化消息重复），日志可去 trace 详情页看。
        break;
      }
      case "step": {
        // 业务步骤事件（read_eval_report 等）——同 log，不展示在对话区。
        break;
      }
      case "end": {
        // 流结束 → 刷新会话详情（拿最终 status）
        setActivity(null);
        void refreshBatches();
        void loadSessionDetail(sessionId);
        // 检查是否需要跳 review-report（pending_review 时，决策 AA）
        setTimeout(async () => {
          try {
            const sessResp = await getEvolveSession(sessionId);
            if (sessResp.status === "pending_review") {
              navigate(`/evolve/${sessionId}/review`);
            }
            setSelectedStatus(sessResp.status);
          } catch {
            // 静默
          }
        }, 500);
        break;
      }
      case "error": {
        toast.error("Agent 执行出错，请查看详情");
        void refreshBatches();
        break;
      }
      default:
        // 未知事件类型——重构后只有上面几种，安全忽略。
        break;
    }
  }

  // ── 发消息（决策 T2 按需触发）───────────────────────────────
  async function handleSend(content: string) {
    if (!selectedSessionId) return;
    // 乐观更新：先在前端追加用户消息
    const optimistic: EvolveMessage = {
      id: `tmp-${Date.now()}`,
      session_id: selectedSessionId,
      role: "user",
      content,
      seq: messages.length + 1,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimistic]);
    try {
      await sendEvolveMessage(selectedSessionId, content);
      // 真实消息后续通过 SSE / loadSessionDetail 同步
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "发送失败");
      // 失败回滚乐观更新
      setMessages((prev) => prev.filter((m) => m.id !== optimistic.id));
    }
  }

  // ── 停止（决策 L：只停输出，会话保留）───────────────────────
  async function handleStop() {
    if (!selectedSessionId) return;
    if (!window.confirm("确定停止 Agent 输出？会话保留，可继续输入。")) return;
    setStopping(true);
    try {
      await stopEvolve(selectedSessionId);
      streamCancelRef.current?.();
      setActivity(null); // 停止成功即撤下活动指示（review finding-15）
      toast.success("已停止");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "停止失败");
    } finally {
      setStopping(false);
    }
  }

  // ── 拍板（决策 C/D/T10）─────────────────────────────────────
  async function handleFinalize() {
    if (!selectedSessionId) return;
    if (acceptedCount === 0) {
      toast.error("至少需要采纳 1 个进化点才能拍板");
      return;
    }
    if (!window.confirm(`确认这 ${acceptedCount} 个进化点，开始落地？拍板后清单冻结。`))
      return;
    setFinalizing(true);
    try {
      await finalizeEvolve(selectedSessionId);
      setSelectedStatus("finalizing");
      toast.success("已拍板，Agent 开始落地…");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "拍板失败");
    } finally {
      setFinalizing(false);
    }
  }

  const canFinalize = selectedStatus === "conversing" && acceptedCount >= 1;
  const selectedAgentName =
    agents.find((a) => a.agent_id === selectedAgentId)?.name ?? null;

  return (
    <div className="evolve-workbench">
      {/* 顶部工具栏 + 落地横幅合并为一个跨列块：evolve-workbench 行模板只有两行
         （auto + 1fr），横幅若单独占一个跨列子项，内容行会掉进隐式 auto 行，
         对话面板被 overflow:hidden 裁掉底部输入框（线上发现 2026-10-01） */}
      <div className="workbench-top">
        {/* 工具栏：常驻「新开会话」入口（交互修正 2026-10-01——不依赖工作台空闲） */}
        <div className="workbench-toolbar">
          <div className="toolbar-current">
            {selectedSessionId ? (
              <>
                <span className="toolbar-label">当前会话</span>
                <code className="session-id">{selectedSessionId.slice(0, 8)}</code>
                {selectedAgentName && <span className="toolbar-agent">· {selectedAgentName}</span>}
              </>
            ) : (
              <span className="toolbar-label muted">未选中会话（历史会话去「进化历史」取）</span>
            )}
          </div>
          <button
            type="button"
            className="start-btn toolbar-new-btn"
            onClick={() => setNewSessionOpen(true)}
            disabled={starting}
          >
            ＋ 新开会话
          </button>
        </div>

        {/* 落地通道占用横幅（FR-003/DEC-004：聊天并行、落地排队） */}
        {landingOccupied && (
          <div className="landing-channel-banner">
            落地通道占用中——另一会话正在落地/待审。对话不受影响；拍板需先等通道释放（发布或丢弃占用会话）。
          </div>
        )}
      </div>
      {/* 中：对话区（原左侧历史已移到独立「进化历史」Tab）*/}
      <ConversationPanel
        selectedSessionId={selectedSessionId}
        status={selectedStatus}
        messages={messages}
        points={points}
        agents={agents}
        hasAnySession={hasAnySession}
        selectedAgentName={selectedAgentName}
        stopping={stopping}
        highlightedPointId={highlightedPointId}
        activity={activity}
        onOpenNewSession={() => setNewSessionOpen(true)}
        onSend={handleSend}
        onStop={handleStop}
        onPointHover={setHighlightedPointId}
      />

      {/* 新开会话对话框（独立组件：Agent 选择/新建/管理 + 可选批次） */}
      <NewSessionDialog
        open={newSessionOpen}
        agents={agents}
        selectedAgentId={selectedAgentId}
        batches={batches}
        starting={starting}
        onClose={() => setNewSessionOpen(false)}
        onSelectAgent={handleSelectAgent}
        onAgentsChanged={() => void refreshAgents()}
        onStart={handleStart}
      />

      {/* 右：进化点浮窗 */}
      <PointsDrawer
        points={points}
        acceptedCount={acceptedCount}
        canFinalize={canFinalize}
        finalizing={finalizing}
        highlightedPointId={highlightedPointId}
        onPointClick={(id) => setHighlightedPointId(id)}
        onFinalize={handleFinalize}
      />
    </div>
  );
}
