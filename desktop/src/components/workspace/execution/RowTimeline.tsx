/**
 * RowTimeline —— 行级交错时间线（REQ-20261018-235653，FR-003/FR-005/FR-008）。
 *
 * 行事件（FR-001）落成的 message.rows 按行序号渲染，四种形态：
 * - 思考块：streaming 纯文本整行流，终态折叠「思考 · N 秒」点开整块 Markdown 回看
 *   （对齐 ThinkingTimeline，REQ-20261021-100317 FR-002）
 * - 正文块：markdown 常驻展开——中间正文按发生位置可见，不垫底（FR-003）
 * - 工具行：紧凑单行（人话名 + 状态/耗时）
 * - 子代理行：单行聚合（名称/轮次/状态/耗时），展开只看摘要（FR-005，DEC-002）
 *
 * 旧消息无 rows → 不进本组件（FR-007 双轨，走 ThinkingTimeline）。
 */
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { markdownTableComponents } from "../../../lib/markdown-components";
import type { TimelineRow } from "@/lib/types";
import { TOOL_DISPLAY_NAMES } from "@/lib/yan-copy";

function formatElapsed(ms: number): string {
  const sec = Math.max(0, Math.round(ms / 1000));
  return sec < 60 ? `${sec} 秒` : `${Math.floor(sec / 60)} 分 ${sec % 60} 秒`;
}

/** streaming 态只渲染已完成行（换行符之前的行），与 ThinkingTimeline 同口径：
 *  未写完的尾行不显示，等换行后整行出现——避免 token 级逐字闪动。 */
function completedLines(text: string): string {
  const idx = text.lastIndexOf("\n");
  return idx === -1 ? "" : text.slice(0, idx + 1);
}

interface RowTimelineProps {
  rows: TimelineRow[];
  loading?: boolean;
}

export function RowTimeline({ rows }: RowTimelineProps) {
  return (
    <div className="yan-thought-timeline" data-testid="row-timeline">
      {rows.map((row) => {
        switch (row.kind) {
          case "reasoning":
            return <ReasoningRowView key={row.id} row={row} />;
          case "text":
            return <TextRowView key={row.id} row={row} />;
          case "tool":
            return <ToolRowView key={row.id} row={row} />;
          default:
            return <SubagentRowView key={row.id} row={row} />;
        }
      })}
    </div>
  );
}

/** 思考块：折叠触发器 + 展开正文（与 ThinkingTimeline 的 ThoughtRow 同交互语义）。 */
function ReasoningRowView({ row }: { row: TimelineRow }) {
  const [userExpanded, setUserExpanded] = useState<boolean | null>(null);
  const live = row.status === "streaming";
  const expanded = userExpanded ?? live;

  const duration =
    row.status === "streaming"
      ? null
      : formatElapsed((row.endedAt ?? row.startedAt) - row.startedAt);

  return (
    <div className="yan-thought-row" data-status={row.status}>
      <button
        type="button"
        className="yan-thought-trigger"
        data-testid={`row-thought-trigger-${row.id}`}
        onClick={() => setUserExpanded(!expanded)}
        aria-expanded={expanded}
      >
        <span className="yan-status-dot" data-status={live ? "running" : row.status === "interrupted" || row.status === "failed" ? "failed" : "completed"} />
        {row.label ? <span className="yan-thought-label">{row.label}</span> : null}
        {live ? (
          <span className="yan-thought-live-title">正在思考</span>
        ) : row.status === "interrupted" || row.status === "failed" ? (
          <span className="yan-thought-live-title yan-thought-interrupted">思考中断</span>
        ) : (
          <span className="yan-thought-done-title">思考{duration ? ` · ${duration}` : ""}</span>
        )}
        <span className="yan-thought-caret">{expanded ? "▴" : "▾"}</span>
      </button>
      {expanded ? <ThoughtText text={live ? completedLines(row.text ?? "") : row.text ?? ""} live={live} /> : null}
    </div>
  );
}

/** 展开态思考正文：streaming 纯文本整行流；终态整块 Markdown（与 ThinkingTimeline 同口径）。 */
function ThoughtText({ text, live }: { text: string; live: boolean }) {
  const boxRef = useRef<HTMLDivElement>(null);
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
    <div ref={boxRef} className="yan-thought-text" data-testid="row-thought-text" onScroll={handleScroll}>
      {live ? (
        lines.map((line, i) => (
          <p key={i} className="yan-thought-para">{line}</p>
        ))
      ) : (
        <div className="yan-thought-markdown">
          <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={markdownTableComponents}>
            {text}
          </ReactMarkdown>
        </div>
      )}
    </div>
  );
}

/** 正文块：markdown 常驻展开（中间正文按发生位置可见，FR-003）。 */
function TextRowView({ row }: { row: TimelineRow }) {
  return (
    <div className="yan-text-row" data-status={row.status} data-testid={`yan-text-row-${row.id}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={markdownTableComponents}>
        {row.text ?? ""}
      </ReactMarkdown>
    </div>
  );
}

/** 工具行：紧凑单行（人话名 + 状态/耗时），与 ThinkingTimeline 的 ToolRow 同口径。
 *  data-status 映射到旧工具行样式值（running/done/failed）。 */
function ToolRowView({ row }: { row: TimelineRow }) {
  const now = Date.now();
  const start = row.startedAt;
  const end = row.status === "streaming" ? now : row.endedAt ?? now;
  const running = row.status === "streaming";
  const elapsed = running ? `已 ${formatElapsed(end - start)}` : formatElapsed(end - start);
  const name = TOOL_DISPLAY_NAMES[row.toolName ?? ""] ?? row.toolName ?? "工具";
  const displayStatus = running ? "running" : row.status === "complete" ? "done" : "failed";

  return (
    <div className="yan-tool-row" data-status={displayStatus} data-testid={`row-tool-${row.id}`}>
      <span className="yan-tool-row-mark">
        {row.status === "complete" ? "✓" : row.status === "failed" ? "✗" : "●"}
      </span>
      <span className="yan-tool-row-name">{name}</span>
      <span className="yan-tool-row-elapsed">{elapsed}</span>
    </div>
  );
}

/** 子代理聚合行：单行（名称/轮次/状态/耗时），展开只看摘要（FR-005，DEC-002）。 */
function SubagentRowView({ row }: { row: TimelineRow }) {
  const [open, setOpen] = useState(false);
  const now = Date.now();
  const name = TOOL_DISPLAY_NAMES[row.subagentType ?? ""] ?? row.subagentType ?? "子代理";
  const running = row.status === "streaming";
  const elapsed = formatElapsed((running ? now : row.endedAt ?? now) - row.startedAt);

  const statusText = running
    ? `进行中 · 已 ${elapsed}`
    : row.status === "failed"
      ? `失败 · ${elapsed}`
      : row.status === "interrupted"
        ? `中断 · ${elapsed}`
        : `完成 · ${elapsed}`;

  return (
    <div className="yan-subagent-row" data-status={row.status}>
      <button
        type="button"
        className="yan-subagent-trigger"
        data-testid={`row-subagent-${row.id}`}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="yan-status-dot" data-status={running ? "running" : row.status === "failed" || row.status === "interrupted" ? "failed" : "completed"} />
        <span className="yan-subagent-name">{name}</span>
        {row.iteration != null ? <span className="yan-subagent-iteration">第 {row.iteration} 轮</span> : null}
        <span className="yan-subagent-status">{statusText}</span>
        {row.summary ? <span className="yan-thought-caret">{open ? "▴" : "▾"}</span> : null}
      </button>
      {open && row.summary ? (
        <div className="yan-subagent-summary" data-testid={`row-subagent-summary-${row.id}`}>{row.summary}</div>
      ) : null}
    </div>
  );
}
