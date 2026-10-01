/**
 * ThinkingView —— 思考流·默认折叠摘要
 *
 * 触发：收到 trace_event（run_start）且未进入 writing 阶段。
 *
 * 折叠态（默认）：拟人化摘要一行 + 脉动圆点。
 *   FR-004：文案按当前运行中的工具信号选主题池，工具一变文案立即变；
 *           映射未覆盖的工具名降级「工具人话名进行中...」。
 *   FR-005：同一主题停留超 THINKING_ROTATE_MS 换句，不重复最近 2 句；
 *           渲染本身不重抽（旧版 render-per-random 导致文案狂跳）。
 *   FR-007：图片流（activeStreamKind=image）用图片主题池。
 * 展开态：显示真实 reasoning 文本（来自 activeReasoning，P2 reasoning_stream）；
 *         无 reasoning 时显示执行步骤列表（FR-006，见 StepsList）。
 */
import { useEffect, useRef, useState } from "react";
import type { ChatMessage } from "@/lib/types";
import type { StageFlow } from "@/lib/stage";
import {
  THINKING_NO_REPEAT,
  THINKING_ROTATE_MS,
  TOOL_DISPLAY_NAMES,
  getStageDisplayName,
  pickThinkingCopy,
  resolveThinkingTheme,
} from "@/lib/yan-copy";
import { useExecutionStore } from "@/stores/execution";

interface ThinkingViewProps {
  message: ChatMessage;
  stageFlow: StageFlow | null;
}

export function ThinkingView({ message, stageFlow }: ThinkingViewProps) {
  const [expanded, setExpanded] = useState(false);
  // T22: 从 executionStore 读瞬态 reasoning（不持久化）
  const activeReasoning = useExecutionStore((s) => s.activeReasoning);
  const activeStreamKind = useExecutionStore((s) => s.activeStreamKind);

  // FR-004 信号：优先当前 running tool 的 subagentType，降级 stageFlow 运行中阶段
  const runningTool = message.tools?.findLast((t) => t.status === "running");
  const theme = resolveThinkingTheme({
    toolName: runningTool?.name,
    subagentType: runningTool?.subagentType ?? stageFlow?.stages.find((s) => s.status === "running")?.type,
    streamKind: activeStreamKind,
  });
  const stageType = runningTool?.subagentType ?? stageFlow?.stages.find((s) => s.status === "running")?.type ?? runningTool?.subagentName;
  const stageName = getStageDisplayName(stageType);
  const iterationSuffix = runningTool?.iteration && runningTool.iteration > 1 ? `（第 ${runningTool.iteration} 轮）` : "";

  // FR-005：文案状态 + 最近用过的记录（新的在后）
  const [copy, setCopy] = useState(() => pickThinkingCopy(theme));
  const recentRef = useRef<string[]>([copy]);

  // FR-004/005：主题（工具信号）变化或挂载 → 立即取本主题新文案、重置轮播历史、
  // 起新轮播定时器。合并为单一 effect：换文案与重置历史原子发生，recentRef
  // 不会出现「interval 记录被 theme 重置丢弃」的交错（防重复窗口破裂）。
  // 卸载/换主题时清理定时器（FR-008）。
  useEffect(() => {
    const next = pickThinkingCopy(theme, recentRef.current);
    setCopy(next);
    recentRef.current = [next];

    const timer = setInterval(() => {
      const rotated = pickThinkingCopy(theme, recentRef.current);
      setCopy(rotated);
      recentRef.current = [...recentRef.current, rotated].slice(-(THINKING_NO_REPEAT + 1));
    }, THINKING_ROTATE_MS);
    return () => clearInterval(timer);
  }, [theme]);

  // FR-004 降级：无映射的工具名 → 「工具人话名进行中...」
  const summaryText =
    !theme && runningTool?.name
      ? `${TOOL_DISPLAY_NAMES[runningTool.name] ?? runningTool.name}进行中...`
      : copy;

  return (
    <div className="yan-thinking" data-phase="thinking">
      <button
        type="button"
        className="yan-thinking-summary"
        onClick={() => setExpanded((v) => !v)}
      >
        <span className="yan-status-dot" data-status="running" />
        <span className="yan-thinking-text">{summaryText}{iterationSuffix}</span>
        <span className="yan-thinking-stage">{stageName}</span>
        {activeReasoning ? <span className="yan-thinking-expand-hint">{expanded ? "▴" : "▾"}</span> : null}
      </button>

      {expanded ? (
        <div className="yan-thinking-detail">
          {activeReasoning ? (
            <pre className="yan-thinking-reasoning">{activeReasoning}</pre>
          ) : (
            <StepsList message={message} stageName={stageName} />
          )}
        </div>
      ) : null}
    </div>
  );
}

/**
 * FR-006 展开态步骤列表：当前动作（含耗时）+ 最近至多 5 步明细。
 * ✓ 完成（耗时）· ● 进行中 · ✗ 失败（标红）。tools 为空时显示当前阶段文案。
 */
function StepsList({ message, stageName }: { message: ChatMessage; stageName: string }) {
  const tools = message.tools ?? [];
  if (tools.length === 0) {
    return <p className="yan-thinking-placeholder">小衍正在{stageName}...</p>;
  }

  const now = Date.now();
  const elapsed = (tool: typeof tools[number]): string => {
    const start = tool.startedAt ?? now;
    const end = tool.status === "running" ? now : tool.endedAt ?? now;
    const ms = Math.max(0, end - start);
    return ms < 60_000 ? `${Math.round(ms / 1000)} 秒` : `${Math.floor(ms / 60_000)} 分 ${Math.round((ms % 60_000) / 1000)} 秒`;
  };

  const running = tools.find((t) => t.status === "running");
  const recent = tools.slice(-5);

  return (
    <div className="yan-thinking-steps">
      {running ? (
        <p className="yan-thinking-steps-current">
          {TOOL_DISPLAY_NAMES[running.name] ?? running.name}
          {running.subagentType ? `（${TOOL_DISPLAY_NAMES[running.subagentType] ?? running.subagentType}）` : ""}
          · 已 {elapsed(running)}
        </p>
      ) : null}
      <ul className="yan-thinking-steps-list">
        {recent.map((tool) => (
          <li key={tool.key} className="yan-thinking-step" data-status={tool.status}>
            <span className="yan-thinking-step-mark">
              {tool.status === "done" ? "✓" : tool.status === "failed" ? "✗" : "●"}
            </span>
            <span className="yan-thinking-step-name">{TOOL_DISPLAY_NAMES[tool.name] ?? tool.name}</span>
            <span className="yan-thinking-step-elapsed">{tool.status === "running" ? "进行中" : elapsed(tool)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
