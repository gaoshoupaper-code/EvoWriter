/**
 * ExecutionView —— 小衍执行体验容器
 *
 * 按 executionPhase 切换子视图，嵌在 assistant message 内。
 * REQ-20261012-202540 FR-004（DEC-006）：执行区默认压成一行状态卡
 * （ExecutionStatusCard），点开才看思考流与工具时间线；终态摘要卡 + 交付/失败/停止视图。
 *
 * REQ-20261010-182114 沿袭：
 * - 写作域思考态走 ThinkingTimeline（作为状态卡的展开内容）
 * - 图片流（activeStreamKind=image）维持拟人 ThinkingView（DEC-006）
 * - asking/终态保留思考时间线可回看（DEC-003/007）
 */
import type { ChatMessage, ExecutionPhase } from "@/lib/types";
import type { StageFlow } from "@/lib/stage";
import { derivePhaseFromMessage } from "@/lib/execution-phase";
import { useExecutionStore } from "@/stores/execution";
import { BootingView } from "./execution/BootingView";
import { ThinkingView } from "./execution/ThinkingView";
import { ThinkingTimeline } from "./execution/ThinkingTimeline";
import { DeliveryCeremony } from "./execution/DeliveryCeremony";
import { FailedView } from "./execution/FailedView";
import { StoppedView } from "./execution/StoppedView";
import { HistoryFolded } from "./execution/HistoryFolded";
import { ExecutionStatusCard, formatDurationZh } from "./execution/ExecutionStatusCard";

interface ExecutionViewProps {
  message: ChatMessage;
  loading: boolean;
  isLastAssistant: boolean; // 是否为最后一条 assistant（活跃执行中）
  stageFlow: StageFlow | null;
  onRetry?: () => void;
}

/** 终态摘要（FR-004）：✓ 完成 · N 轮 · 耗时 */
function completedSummary(stageFlow: StageFlow | null): string {
  const writingStage = stageFlow?.stages.find((s) => s.type === "storybuilding");
  const rounds = writingStage?.subSteps.length ?? 0;
  const duration = formatDurationZh(stageFlow?.totalDurationMs ?? null);
  const parts = ["✓ 完成"];
  if (rounds > 0) parts.push(`${rounds} 轮`);
  if (duration) parts.push(`耗时 ${duration}`);
  return parts.join(" · ");
}

export function ExecutionView({ message, loading, isLastAssistant, stageFlow, onRetry }: ExecutionViewProps) {
  // T17: 优先用 message.executionPhase（store 写入），兜底用临时派生
  const phase: ExecutionPhase = message.executionPhase ?? derivePhaseFromMessage(message);
  // DEC-006：图片流思考态维持拟人 ThinkingView，不进思考时间线
  const activeStreamKind = useExecutionStore((s) => s.activeStreamKind);
  const imageStream = activeStreamKind === "image";
  // 思考时间线（有思考段才渲染；终态/中断保留可回看，DEC-003/007）
  const timeline = message.thoughts?.length ? <ThinkingTimeline message={message} loading={loading} /> : null;

  // 历史消息（非活跃）：摘要卡形态（与当前消息的状态卡统一）
  if (!isLastAssistant && (phase === "delivering" || phase === "idle")) {
    return <HistoryFolded stageFlow={stageFlow} message={message} />;
  }

  switch (phase) {
    case "booting":
      // FR-002：reasoning 先于首个 tool_call 到达时直接进时间线，不停留在黑屏期
      return timeline ?? <BootingView />;
    case "thinking":
      if (imageStream) return <ThinkingView message={message} stageFlow={stageFlow} />;
      return (
        <ExecutionStatusCard message={message} loading={loading}>
          {timeline ?? <ThinkingTimeline message={message} loading={loading} />}
        </ExecutionStatusCard>
      );
    case "asking":
      // asking 态的 HITL UI 由 ChatPanel 外层渲染（InterviewOptions/ImageReviewCard）
      // 状态卡保留思考时间线到中断点（DEC-007）
      return (
        <ExecutionStatusCard message={message} summary="已暂停 · 等你回答">
          {timeline}
        </ExecutionStatusCard>
      );
    case "delivering":
      return (
        <>
          <ExecutionStatusCard message={message} summary={completedSummary(stageFlow)} tone="completed">
            {timeline}
          </ExecutionStatusCard>
          <DeliveryCeremony message={message} stageFlow={stageFlow} />
        </>
      );
    case "failed":
      return (
        <>
          <ExecutionStatusCard message={message} summary="✗ 没能完成" tone="failed">
            {timeline}
          </ExecutionStatusCard>
          <FailedView message={message} onRetry={onRetry} />
        </>
      );
    case "stopped":
      return (
        <>
          <ExecutionStatusCard message={message} summary="已停下" tone="completed">
            {timeline}
          </ExecutionStatusCard>
          <StoppedView message={message} onRetry={onRetry} />
        </>
      );
    default:
      return null;
  }
}
