import { useEffect, useRef, useState } from "react";
import type { EvolveAgent, EvolveMessage, EvolvePoint } from "@/lib/api";
import type { TimelineRow } from "@/lib/rows";
import AgentLaunchCard from "./AgentLaunchCard";
import EvolveMessageBubble from "./EvolveMessageBubble";
import { RowTimeline, RoundPendingIndicator } from "./RowTimeline";

/**
 * 中部对话区（决策 J/K/L/X；形态对齐客户端 ChatPanel，REQ-20261019-183921 FR-002/003）。
 *
 * 视图状态：
 *   - idle（无选中会话）：空态引导卡（AgentLaunchCard）
 *   - 活跃（conversing/running/finalizing）：对话流 + 行时间线 + 输入框
 *   - terminal（published/discarded/failed/cancelled）：只读对话流
 *
 * 零开场（FR-001/FR-002）：新建会话消息列表为空时显示静态引导语
 * 「直接输入你想关注的问题」——不经模型、零等待。
 *
 * 消息布局（FR-003，照客户端）：单列；用户消息右侧小气泡纯文本；
 * Agent 消息无气泡全宽 markdown + 角色标签 + 内嵌进化点卡片；
 * tool/system 消息单行小字。
 *
 * 进行中时间线（FR-004）：round_running 时消息流末尾渲染 liveRows
 * （思考折叠块/正文逐段生长/工具行）；无行时兜底「正在思考」。
 *
 * 输入框（FR-003/FR-005，照客户端 composer）：Enter 发送、Shift+Enter 换行、
 * 中文输入法组词保护、自动伸缩；轮次进行中禁用且发送键变停止键。
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
  liveRows: TimelineRow[]; // 当前轮行时间线（进行中）
  roundRunning: boolean; // 轮次运行中（round_start→round_end）
  onOpenNewSession: () => void;
  onOpenReview?: () => void; // 待审查时跳审查报告页（发布/丢弃入口）
  onSend: (content: string) => void;
  onStop: () => void;
  onPointHover: (pointId: string | null) => void;
}

const COMPOSER_MAX_HEIGHT = 3 * 1.65 * 16; // 3 行自动伸缩上限（行高 1.65 × 16px）

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
  liveRows,
  roundRunning,
  onOpenNewSession,
  onOpenReview,
  onSend,
  onStop,
  onPointHover,
}: Props) {
  const [input, setInput] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const messageRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  // 自动滚到底（新消息/新行到达时）
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, liveRows]);

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
  const isPendingReview = status === "pending_review";
  const isTerminal =
    status === "published" ||
    status === "discarded" ||
    status === "failed" ||
    status === "cancelled" ||
    status === "cancel_timeout";
  // 轮次进行中输入锁定（roundRunning 由 SSE round_start/round_end 驱动）；
  // finalizing 等落地完成同样锁定
  const locked = roundRunning || status === "finalizing" || status === "running" || status === "cancelling";
  const canInput = isConversing && !locked;

  function handleSend() {
    const content = input.trim();
    if (!content || !canInput) return;
    setInput("");
    onSend(content);
    // 收起输入框高度并回焦
    if (inputRef.current) {
      inputRef.current.style.height = "auto";
      inputRef.current.focus();
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // 中文输入法组词中的 Enter 不发送（对齐客户端 isComposing 保护）
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleSend();
    }
  }

  // 自动伸缩：内容撑高到 3 行上限（对齐客户端 collapsedHeight 口径）
  function autoResize(el: HTMLTextAreaElement) {
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, COMPOSER_MAX_HEIGHT)}px`;
  }

  const statusBadge = STATUS_LABEL[status ?? ""] ?? status;
  const showTimeline = roundRunning || liveRows.length > 0;

  return (
    <section className="conversation-panel">
      <header className="conversation-header">
        <div className="conv-status">
          <span className={`status-dot status-${status}`} />
          <span className="status-text">{statusBadge}</span>
          <code className="session-id">{selectedSessionId.slice(0, 8)}</code>
        </div>
      </header>

      <div className="message-list">
        {messages.length === 0 && !showTimeline ? (
          // FR-002 零开场空态：静态引导语（不经模型生成）
          <div className="conv-empty">
            <div className="empty-glyph">🧬</div>
            <p className="empty-text">直接输入你想关注的问题</p>
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
            {/* 进行中轮次的行时间线（FR-004）：思考块/正文生长/工具行 */}
            {showTimeline && (
              <div className="ev-live-round">
                {liveRows.length > 0 ? (
                  <RowTimeline rows={liveRows} />
                ) : (
                  roundRunning && <RoundPendingIndicator />
                )}
              </div>
            )}
            <div ref={messagesEndRef} />
          </>
        )}
      </div>

      {/* 待审查：composer 换成审查行动条（发布/丢弃入口在审查报告页）。
          自动跳转只在落地结束那一瞬触发一次，错过就没有入口（线上发现 2026-10-01），
          这里给 pending_review 一个常驻入口 */}
      {isPendingReview && onOpenReview ? (
        <footer className="ev-composer review-cta">
          <span className="review-cta-note">落地已完成，等待人工审查后发布</span>
          <button type="button" className="ev-send-button" onClick={onOpenReview}>
            查看审查报告并发布 →
          </button>
        </footer>
      ) : (
        <footer className="ev-composer">
          <div className="ev-input-wrap">
            <textarea
              ref={inputRef}
              className="ev-input"
              placeholder={
                canInput
                  ? "输入你关注的问题…（Enter 发送，Shift+Enter 换行）"
                  : isTerminal
                    ? "会话已结束"
                    : "Agent 正在工作…"
              }
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                autoResize(e.target);
              }}
              onKeyDown={handleKeyDown}
              disabled={!canInput}
              rows={1}
            />
          </div>
          <div className="ev-composer-actions">
            {locked && !isTerminal ? (
              // 轮次进行中：发送键位置换成停止键（FR-005，对齐客户端）
              <button
                type="button"
                className="ev-stop-button"
                onClick={onStop}
                disabled={stopping}
                title={stopping ? "停止中…" : "停止本轮"}
                aria-label="停止"
              >
                {stopping ? "…" : "■"}
              </button>
            ) : (
              <button
                type="button"
                className="ev-send-button"
                onClick={handleSend}
                disabled={!canInput || !input.trim()}
                title="发送"
                aria-label="发送"
              >
                ↑
              </button>
            )}
          </div>
        </footer>
      )}
    </section>
  );
}

const STATUS_LABEL: Record<string, string> = {
  running: "处理中",
  conversing: "对话共创中",
  finalizing: "落地中",
  pending_review: "待审查",
  published: "已发版",
  discarded: "已丢弃",
  failed: "失败",
  cancelled: "已取消",
  cancel_timeout: "停止超时",
};
