import { useEffect, useRef, useState } from "react";
import type { EvolveAgent, EvolveMessage, EvolvePoint } from "@/lib/api";
import AgentLaunchCard from "./AgentLaunchCard";
import EvolveMessageBubble from "./EvolveMessageBubble";

/**
 * 中部对话区（决策 J/K/L/X）。
 *
 * 三种视图状态：
 *   - idle（无选中会话）：空态引导卡（AgentLaunchCard——「新开会话」统一走
 *     NewSessionDialog 独立组件，工具栏常驻可点，交互修正 2026-10-01）
 *   - conversing / running / finalizing：显示对话流 + 输入框（conversing 可输入）
 *   - terminal（published/discarded/failed/cancelled）：只读对话流
 *
 * 输入框（决策 X）：纯文本 + Enter 发送，Shift+Enter 换行。conversing 状态可输入，
 * 其他状态禁用（finalizing 等待落地完成）。
 *
 * Agent 主动开场（决策 J）：inspect round 跑完后会有一条 assistant 消息。
 *
 * 双向高亮联动（决策 N）：
 *   - 点击浮窗进化点 → 滚动到本区对应消息（onScrollToMessage 触发）
 *   - hover 消息里的进化点卡片 → 触发 onPointHover（浮窗高亮该项）
 */
interface Props {
  selectedSessionId: string | null;
  status: string | null;
  messages: EvolveMessage[];
  points: EvolvePoint[];
  agents: EvolveAgent[];
  hasAnySession: boolean;
  selectedAgentName: string | null;
  stopping: boolean;
  highlightedPointId: string | null; // 来自浮窗点击
  activity: { label: string; at: number } | null; // FR-005 运行中活动信号（父层已做过期隐藏）
  onOpenNewSession: () => void;
  onSend: (content: string) => void;
  onStop: () => void;
  onPointHover: (pointId: string | null) => void;
}

export default function ConversationPanel({
  selectedSessionId,
  status,
  messages,
  points,
  agents,
  hasAnySession,
  selectedAgentName,
  stopping,
  highlightedPointId,
  activity,
  onOpenNewSession,
  onSend,
  onStop,
  onPointHover,
}: Props) {
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messageRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  // 自动滚到底（新消息到达时）
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  // 浮窗点击进化点 → 滚动到对应消息（决策 N 双向联动）
  useEffect(() => {
    if (!highlightedPointId) return;
    // 找到 related_points 含该 id 的消息
    const targetMsg = messages.find(
      (m) => m.related_points && m.related_points.includes(highlightedPointId),
    );
    if (targetMsg) {
      const el = messageRefs.current.get(targetMsg.id);
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    // 3 秒后自动清高亮（参考 TraceChainTimeline 范式）
    const timer = setTimeout(() => onPointHover(null), 3000);
    return () => clearTimeout(timer);
  }, [highlightedPointId, messages, onPointHover]);

  // ── idle 视图：空态引导（开新会话走独立组件 NewSessionDialog）──
  if (!selectedSessionId) {
    return (
      <AgentLaunchCard
        agents={agents}
        hasAnySession={hasAnySession}
        selectedAgentName={selectedAgentName}
        onOpenNewSession={onOpenNewSession}
      />
    );
  }

  // ── 对话视图 ────────────────────────────────────────────────
  const isConversing = status === "conversing";
  const isRunning = status === "running" || status === "finalizing";
  const isTerminal =
    status === "published" ||
    status === "discarded" ||
    status === "failed" ||
    status === "cancelled";
  const canInput = isConversing;

  async function handleSend() {
    const content = input.trim();
    if (!content || !canInput) return;
    setSending(true);
    setInput("");
    try {
      await onSend(content);
    } finally {
      setSending(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
  }

  const statusBadge = STATUS_LABEL[status ?? ""] ?? status;

  return (
    <section className="conversation-panel">
      <header className="conversation-header">
        <div className="conv-status">
          <span className={`status-dot status-${status}`} />
          <span className="status-text">{statusBadge}</span>
          <code className="session-id">{selectedSessionId.slice(0, 8)}</code>
        </div>
        {(isRunning || isConversing) && (
          <button
            type="button"
            className="stop-btn"
            onClick={onStop}
            disabled={stopping}
          >
            {stopping ? "停止中…" : "停止"}
          </button>
        )}
      </header>

      <div className="message-list">
        {/* FR-005 运行中活动指示：取证/思考期间让用户看到 Agent 还活着 */}
        {activity && (
          <div className="activity-indicator" role="status">
            <span className="activity-dot" aria-hidden />
            <span className="activity-label">{activity.label}</span>
          </div>
        )}
        {messages.length === 0 ? (
          <div className="conv-empty">
            <div className="empty-glyph">⏳</div>
            <p className="empty-text">
              {isRunning
                ? "Agent 正在准备开场分析（基于作品概览），请稍候…"
                : isConversing
                  ? "等待 Agent 回复…" // conversing 但无消息：用户已发首条消息，等 Agent 回复
                  : "等待 Agent 发出开场白…"}
            </p>
          </div>
        ) : (
          <>
            {messages.map((msg) => (
              <EvolveMessageBubble
                key={msg.id}
                ref={(el: HTMLDivElement | null) => {
                  if (el) messageRefs.current.set(msg.id, el);
                  else messageRefs.current.delete(msg.id);
                }}
                message={msg}
                points={points}
                highlightedPointId={highlightedPointId}
                onPointClick={onPointHover}
              />
            ))}
            <div ref={messagesEndRef} />
          </>
        )}
      </div>

      <footer className="composer">
        <textarea
          className="composer-input"
          placeholder={
            canInput
              ? "和 Agent 讨论改进点…（Enter 发送，Shift+Enter 换行）"
              : isTerminal
                ? "会话已结束"
                : "Agent 正在工作，请稍候…"
          }
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={!canInput}
          rows={2}
        />
        <button
          type="button"
          className="send-btn"
          onClick={handleSend}
          disabled={!canInput || sending || !input.trim()}
        >
          {sending ? "发送中…" : "发送"}
        </button>
      </footer>
    </section>
  );
}

const STATUS_LABEL: Record<string, string> = {
  running: "探查中",
  conversing: "对话共创中",
  finalizing: "落地中",
  pending_review: "待审查",
  published: "已发版",
  discarded: "已丢弃",
  failed: "失败",
  cancelled: "已取消",
};
