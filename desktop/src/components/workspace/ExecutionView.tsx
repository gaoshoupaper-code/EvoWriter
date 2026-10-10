/**
 * ExecutionView —— 小衍执行体验容器
 *
 * 按 executionPhase 切换子视图，取代 StageFlowView 在对话流中的角色。
 * 嵌入 assistant message 内，渲染执行过程的"有温度"反馈。
 *
 * T17 正式版：phase 优先读 message.executionPhase（由 executionStore
 * 在每次 message 更新后通过 derivePhaseFromMessage 写入）。
 * 兜底：若 executionPhase 缺失（如历史消息未被 store 处理），临时派生。
 *
 * 历史消息（非活跃 trace）显示折叠态（delivered + 可展开执行过程摘要）。
 *
 * REQ-20261010-182114：
 * - 写作域思考态走 ThinkingTimeline（真实思考流时间线，DEC-001/005）
 * - 图片流（activeStreamKind=image）维持拟人 ThinkingView（DEC-006）
 * - asking/终态（delivering/failed/stopped）保留思考时间线可回看（DEC-003/007）
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

interface ExecutionViewProps {
  message: ChatMessage;
  loading: boolean;
  isLastAssistant: boolean; // 是否为最后一条 assistant（活跃执行中）
  stageFlow: StageFlow | null;
  onRetry?: () => void;
}

export function ExecutionView({ message, loading, isLastAssistant, stageFlow, onRetry }: ExecutionViewProps) {
  // T17: 优先用 message.executionPhase（store 写入），兜底用临时派生
  const phase: ExecutionPhase = message.executionPhase ?? derivePhaseFromMessage(message);
  // DEC-006：图片流思考态维持拟人 ThinkingView，不进思考时间线
  const activeStreamKind = useExecutionStore((s) => s.activeStreamKind);
  const imageStream = activeStreamKind === "image";
  // 思考时间线（有思考段才渲染；终态/中断保留可回看，DEC-003/007）
  const timeline = message.thoughts?.length ? <ThinkingTimeline message={message} loading={loading} /> : null;

  // 历史消息（非活跃）：显示折叠态
  if (!isLastAssistant && (phase === "delivering" || phase === "idle")) {
    return <HistoryFolded stageFlow={stageFlow} message={message} />;
  }

  switch (phase) {
    case "booting":
      // FR-002：reasoning 先于首个 tool_call 到达时直接进时间线，不停留在黑屏期
      return timeline ?? <BootingView />;
    case "thinking":
      return imageStream ? <ThinkingView message={message} stageFlow={stageFlow} /> : (timeline ?? <ThinkingTimeline message={message} loading={loading} />);
    case "asking":
      // asking 态的 HITL UI 由 ChatPanel 外层渲染（InterviewOptions/ImageReviewCard）
      // ExecutionView 保留思考时间线到中断点（DEC-007），无思考段时不渲染额外内容
      return timeline;
    case "delivering":
      return (
        <>
          {timeline}
          <DeliveryCeremony message={message} stageFlow={stageFlow} />
        </>
      );
    case "failed":
      return (
        <>
          {timeline}
          <FailedView message={message} onRetry={onRetry} />
        </>
      );
    case "stopped":
      return (
        <>
          {timeline}
          <StoppedView message={message} onRetry={onRetry} />
        </>
      );
    default:
      return null;
  }
}
