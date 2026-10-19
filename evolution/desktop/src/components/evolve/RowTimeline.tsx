/**
 * RowTimeline —— 行级交错时间线（REQ-20261019-183921 FR-003，移植自写作端
 * desktop/src/components/workspace/execution/RowTimeline.tsx；进化单体无子代理）。
 *
 * 行数据（SSE row 帧归并 / 消息 rows_json 回放）按行序号渲染，三种形态：
 * - 思考块：streaming 展开实时看行流，终态折叠「思考 · N 秒」可回看
 * - 正文块：markdown 常驻展开（进行中的正文按发生位置可见）
 * - 工具行：紧凑单行（人话名 + 状态/耗时）
 */
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import type { TimelineRow } from "@/lib/rows";

/** 进化工具人话名（展示用；未收录的回退原始名） */
const TOOL_DISPLAY_NAMES: Record<string, string> = {
  list_elements: "查看 harness 要素",
  read_source: "读要素源码",
  list_work_traces: "列作品会话记录",
  read_work_trace: "读会话记录",
  read_current_artifacts: "看当前产物",
  list_artifact_revisions: "列产物修订",
  read_artifact_revision: "读产物修订",
  list_agent_sessions: "翻历史进化会话",
  read_agent_session: "读历史会话",
  propose_evolution_point: "提出进化点",
  update_evolution_point: "更新进化点",
  reject_evolution_point: "否决进化点",
  write_prompt: "写提示词",
  write_middleware: "写中间件",
  write_tool: "写工具",
  write_skill: "写技能",
  write_subagent: "写子代理",
  write_design_doc: "写方案文档",
  write_change_log: "写变更记录",
  edit_source: "改源码",
  validate_changes: "校验改动",
  inspect_middleware_protocol: "查中间件协议",
};

function formatElapsed(ms: number): string {
  const sec = Math.max(0, Math.round(ms / 1000));
  return sec < 60 ? `${sec} 秒` : `${Math.floor(sec / 60)} 分 ${sec % 60} 秒`;
}

/** streaming 态只渲染已完成行（换行符之前的行）：未写完的尾行不显示，
 *  等换行后整行出现——避免 token 级逐字闪动（与写作端同口径）。 */
function completedLines(text: string): string {
  const idx = text.lastIndexOf("\n");
  return idx === -1 ? "" : text.slice(0, idx + 1);
}

interface RowTimelineProps {
  rows: TimelineRow[];
  /** 只渲染思考行（消息 rows 回放：正文/工具由消息与 tool 消息承担，防重复） */
  reasoningOnly?: boolean;
}

export function RowTimeline({ rows, reasoningOnly = false }: RowTimelineProps) {
  const visible = reasoningOnly ? rows.filter((r) => r.kind === "reasoning") : rows;
  return (
    <div className="yan-thought-timeline" data-testid="row-timeline">
      {visible.map((row) => {
        switch (row.kind) {
          case "reasoning":
            return <ReasoningRowView key={row.id} row={row} />;
          case "text":
            return <TextRowView key={row.id} row={row} />;
          default:
            return <ToolRowView key={row.id} row={row} />;
        }
      })}
    </div>
  );
}

/** 思考块：折叠触发器 + 展开正文。 */
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
        <span
          className="yan-status-dot"
          data-status={
            live
              ? "running"
              : row.status === "interrupted" || row.status === "failed"
                ? "failed"
                : "completed"
          }
        />
        {live ? (
          <span className="yan-thought-live-title">正在思考</span>
        ) : row.status === "interrupted" || row.status === "failed" ? (
          <span className="yan-thought-live-title yan-thought-interrupted">思考中断</span>
        ) : (
          <span className="yan-thought-done-title">思考{duration ? ` · ${duration}` : ""}</span>
        )}
        <span className="yan-thought-caret">{expanded ? "▴" : "▾"}</span>
      </button>
      {expanded ? <ThoughtText text={live ? completedLines(row.text ?? "") : row.text ?? ""} /> : null}
    </div>
  );
}

/** 展开态思考正文：一句一段 + 流式吸底跟随。 */
function ThoughtText({ text }: { text: string }) {
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
      {lines.map((line, i) => (
        <p key={i} className="yan-thought-para">{line}</p>
      ))}
    </div>
  );
}

/** 正文块：markdown 常驻展开。 */
function TextRowView({ row }: { row: TimelineRow }) {
  return (
    <div className="yan-text-row" data-status={row.status} data-testid={`yan-text-row-${row.id}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{row.text ?? ""}</ReactMarkdown>
    </div>
  );
}

/** 工具行：紧凑单行（人话名 + 状态/耗时）。 */
function ToolRowView({ row }: { row: TimelineRow }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (row.status !== "streaming") return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [row.status]);

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

/** 进行中兜底状态行：round 在跑但还没有任何行（非流式模型/首字未到）。 */
export function RoundPendingIndicator() {
  return (
    <div className="yan-thought-fallback" role="status">
      <span className="yan-status-dot" data-status="running" />
      <span className="yan-thought-fallback-text">正在思考</span>
    </div>
  );
}
