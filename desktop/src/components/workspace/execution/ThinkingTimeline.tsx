/**
 * ThinkingTimeline —— 思考流时间线（写作域思考态，FR-002/003/004/005）
 *
 * REQ-20261010-182114 / DEC-001/002/005/007/008：
 * - 思考段与工具行按 startedAt 交替成时间线（FR-003）
 * - streaming 段：折叠行「正在思考 + 最新一行」实时滚动（FR-002，DEC-001/008）
 * - done 段：折叠为「思考 · N 秒」，点开回看（DEC-003）
 * - interrupted 段：「思考中断」，保留到中断点（DEC-007）
 * - 展开态：保留模型自然换行、一句一段（DEC-002），纯文本不走 Markdown（FR-007）；
 *   流式追加自动吸底，用户上滚后暂停跟随，滚回底部恢复（FR-004）
 * - 无思考流兜底：「正在思考 + 工具名 + 已耗时」（DEC-004）
 *
 * 图片生成域不进本组件（DEC-006，见 ThinkingView）。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { ChatMessage, ThoughtSegment, ToolStatus } from "@/lib/types";
import { currentTaskLabel } from "@/lib/thoughts";
import { TOOL_DISPLAY_NAMES } from "@/lib/yan-copy";

interface ThinkingTimelineProps {
  message: ChatMessage;
  /** 活跃执行中（驱动无思考流的兜底行）；历史消息传 false */
  loading?: boolean;
}

type TimelineItem =
  | { kind: "thought"; seg: ThoughtSegment; startedAt: number }
  | { kind: "tool"; tool: ToolStatus; startedAt: number };

function formatElapsed(ms: number): string {
  const sec = Math.max(0, Math.round(ms / 1000));
  return sec < 60 ? `${sec} 秒` : `${Math.floor(sec / 60)} 分 ${sec % 60} 秒`;
}

/** 思考文本最后一个非空行（FR-007：折叠行只渲染这一行，DEC-008 直接展示原文）。 */
function lastNonEmptyLine(text: string): string {
  const lines = text.split("\n");
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i].trim();
    if (line) return line;
  }
  return "";
}

export function ThinkingTimeline({ message, loading = false }: ThinkingTimelineProps) {
  const thoughts = message.thoughts ?? [];
  const tools = message.tools ?? [];

  const items = useMemo<TimelineItem[]>(() => {
    const merged: TimelineItem[] = [
      ...thoughts.map((seg) => ({ kind: "thought" as const, seg, startedAt: seg.startedAt })),
      ...tools.map((tool) => ({ kind: "tool" as const, tool, startedAt: tool.startedAt ?? 0 })),
    ];
    // 稳定排序：startedAt 升序，同刻思考段在前（reasoning 先于它触发的工具调用）
    return merged.sort((a, b) => a.startedAt - b.startedAt || (a.kind === "thought" ? -1 : 1));
  }, [thoughts, tools]);

  // 兜底（DEC-004）：无思考流时段显示真实进度行；既无思考也无工具且非活跃 → 不渲染
  if (!thoughts.length) {
    if (!tools.length && !loading) return null;
    return (
      <div className="yan-thought-timeline">
        <FallbackRow message={message} />
        {tools.map((tool) => (
          <ToolRow key={tool.key} tool={tool} />
        ))}
      </div>
    );
  }

  return (
    <div className="yan-thought-timeline">
      {items.map((item) =>
        item.kind === "thought" ? (
          <ThoughtRow key={`thought-${item.seg.id}`} seg={item.seg} />
        ) : (
          <ToolRow key={item.tool.key} tool={item.tool} />
        ),
      )}
    </div>
  );
}

/** 无思考流兜底行：正在思考 + 当前工具/子任务人话名 + 已耗时（DEC-004）。 */
function FallbackRow({ message }: { message: ChatMessage }) {
  const running = message.tools?.findLast((t) => t.status === "running");
  const label = currentTaskLabel(message.tools);
  const now = Date.now();
  const elapsed = running ? formatElapsed(now - (running.startedAt ?? now)) : null;

  return (
    <div className="yan-thought-fallback">
      <span className="yan-status-dot" data-status="running" />
      <span className="yan-thought-fallback-text">
        正在思考
        {label ? ` · ${label}` : ""}
        {elapsed ? ` · 已 ${elapsed}` : ""}
      </span>
    </div>
  );
}

/** 思考段行：折叠触发器（状态 + 摘要）+ 展开正文（FR-002/004）。 */
function ThoughtRow({ seg }: { seg: ThoughtSegment }) {
  const [expanded, setExpanded] = useState(false);
  const liveRef = useRef<HTMLSpanElement>(null);
  const live = seg.status === "streaming";
  const summary = live ? lastNonEmptyLine(seg.text) : "";

  // 滚动摘要（ZCode 同款）：内容增长后把单行视口推到末尾，最新 token 始终可见
  useEffect(() => {
    const el = liveRef.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, [summary]);

  const duration =
    seg.status === "streaming"
      ? null
      : formatElapsed((seg.endedAt ?? seg.startedAt) - seg.startedAt);

  return (
    <div className="yan-thought-row" data-status={seg.status}>
      <button
        type="button"
        className="yan-thought-trigger"
        data-testid={`yan-thought-trigger-${seg.id}`}
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <span className="yan-status-dot" data-status={live ? "running" : seg.status === "interrupted" ? "failed" : "completed"} />
        {seg.label ? <span className="yan-thought-label">{seg.label}</span> : null}
        {live ? (
          <>
            <span className="yan-thought-live-title">正在思考</span>
            <span className="yan-thought-live" ref={liveRef}>{summary}</span>
          </>
        ) : seg.status === "interrupted" ? (
          <span className="yan-thought-live-title yan-thought-interrupted">思考中断</span>
        ) : (
          <span className="yan-thought-done-title">思考{duration ? ` · ${duration}` : ""}</span>
        )}
        <span className="yan-thought-caret">{expanded ? "▴" : "▾"}</span>
      </button>
      {expanded ? <ThoughtText text={seg.text} /> : null}
    </div>
  );
}

/** 展开态思考正文：自然换行切段（DEC-002 一句一段），吸底跟随（FR-004）。 */
function ThoughtText({ text }: { text: string }) {
  const boxRef = useRef<HTMLDivElement>(null);
  // 用户是否贴着底部：贴底才跟随新内容；上滚暂停，滚回底部恢复
  const followRef = useRef(true);

  const handleScroll = () => {
    const el = boxRef.current;
    if (!el) return;
    followRef.current = el.scrollHeight - el.clientHeight - el.scrollTop <= 2;
  };

  useEffect(() => {
    const el = boxRef.current;
    if (el && followRef.current) el.scrollTop = el.scrollHeight;
  }, [text]);

  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);

  return (
    <div ref={boxRef} className="yan-thought-text" data-testid="yan-thought-text" onScroll={handleScroll}>
      {lines.map((line, i) => (
        <p key={i} className="yan-thought-para">{line}</p>
      ))}
    </div>
  );
}

/** 工具行：紧凑单行（工具人话名 + 状态/耗时），穿插在思考段之间（FR-003）。 */
function ToolRow({ tool }: { tool: ToolStatus }) {
  const now = Date.now();
  const start = tool.startedAt ?? now;
  const end = tool.status === "running" ? now : tool.endedAt ?? now;
  const elapsed = tool.status === "running" ? `已 ${formatElapsed(end - start)}` : formatElapsed(end - start);
  const name = TOOL_DISPLAY_NAMES[tool.name] ?? tool.name;
  const subName = tool.subagentType && tool.subagentType !== tool.name
    ? TOOL_DISPLAY_NAMES[tool.subagentType] ?? tool.subagentType
    : null;

  return (
    <div className="yan-tool-row" data-status={tool.status} data-testid={`yan-tool-row-${tool.key}`}>
      <span className="yan-tool-row-mark">
        {tool.status === "done" ? "✓" : tool.status === "failed" ? "✗" : "●"}
      </span>
      <span className="yan-tool-row-name">{name}{subName ? `（${subName}）` : ""}</span>
      <span className="yan-tool-row-elapsed">{elapsed}</span>
    </div>
  );
}
