/**
 * ExecutionStatusCard —— 执行状态卡（REQ-20261012-202540 FR-004，DEC-006）。
 *
 * 对话执行区默认一行：状态图标 + 当前动作人话 + 已耗时（执行中每秒跳动）；
 * 点开才看思考流与工具时间线（children）。对话回归「读内容」，
 * 历史消息与当前消息统一用这个卡（HistoryFolded 的改版形态）。
 */
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { ChatMessage } from "@/lib/types";
import { currentTaskLabel } from "@/lib/thoughts";

/** 耗时人话（全 UI 统一口径）：先取整总秒数再拆分，避免 59.5s 进位出「1 分 60 秒」 */
export function formatDurationZh(ms: number | null | undefined): string | null {
  if (ms == null) return null;
  const sec = Math.max(0, Math.round(ms / 1000));
  return sec < 60 ? `${sec} 秒` : `${Math.floor(sec / 60)} 分 ${sec % 60} 秒`;
}

interface ExecutionStatusCardProps {
  message: ChatMessage;
  /** 活跃执行中（耗时行每秒跳动） */
  loading?: boolean;
  /** 摘要行文案（默认派生当前动作；终态由调用方传入如「✓ 完成 · 3 轮 · 5 分」） */
  summary?: string;
  /** 状态点颜色语义 */
  tone?: "running" | "completed" | "failed";
  /** 展开后的详细内容（思考时间线/工具行/阶段轨迹） */
  children?: ReactNode;
  /** 展开区为空时隐藏折叠交互（纯摘要卡） */
  expandable?: boolean;
  /** 挂载时默认展开（REQ-20261018-235653 FR-003：行消息运行中时间线实时生长） */
  defaultOpen?: boolean;
  /** 流结束时保持展开不自动收拢（行消息提问态：正文时间线保留到中断点，FR-004） */
  keepOpenOnEnd?: boolean;
}

export function ExecutionStatusCard({
  message,
  loading = false,
  summary,
  tone,
  children,
  expandable = true,
  defaultOpen = false,
  keepOpenOnEnd = false,
}: ExecutionStatusCardProps) {
  const [expanded, setExpanded] = useState(defaultOpen);
  // 执行中耗时每秒跳动；终态静止
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!loading) return;
    const timer = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(timer);
  }, [loading]);

  // 完成即收拢（FR-004/AC-005）：loading true→false 时折叠回摘要行。
  // keepOpenOnEnd（REQ-20261018-235653 FR-004）：行消息提问中断时保留展开。
  const prevLoadingRef = useRef(false);
  useEffect(() => {
    if (prevLoadingRef.current && !loading && !keepOpenOnEnd) setExpanded(false);
    prevLoadingRef.current = loading;
  }, [loading, keepOpenOnEnd]);

  const running = message.tools?.findLast((t) => t.status === "running");
  const actionLabel = currentTaskLabel(message.tools);
  const start = running?.startedAt ?? message.thoughts?.[0]?.startedAt;
  const elapsed = loading && start ? formatDurationZh(Date.now() - start) : null;

  const effectiveTone = tone ?? (loading ? "running" : "completed");
  const text =
    summary ??
    (actionLabel ? `正在${actionLabel}` : "正在思考") + (elapsed ? ` · 已 ${elapsed}` : "");
  const canToggle = expandable && !!children;

  return (
    <div className="exec-status-card" data-tone={effectiveTone} data-testid="exec-status-card">
      <button
        type="button"
        className="exec-status-line"
        onClick={() => canToggle && setExpanded((v) => !v)}
        aria-expanded={canToggle ? expanded : undefined}
        disabled={!canToggle}
        data-testid="exec-status-line"
      >
        <span className="exec-status-dot" data-tone={effectiveTone} aria-hidden="true" />
        <span className="exec-status-text">{text}</span>
        {canToggle ? <span className="exec-status-caret">{expanded ? "▴" : "▾"}</span> : null}
      </button>
      {expanded && children ? <div className="exec-status-detail">{children}</div> : null}
    </div>
  );
}
