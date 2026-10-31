/**
 * ExecutionView —— 小衍执行体验容器
 *
 * 按 executionPhase 切换子视图，嵌在 assistant message 内。
 * REQ-20261012-202540 FR-004（DEC-006）：执行区默认压成一行状态卡
 * （ExecutionStatusCard），点开才看思考流与工具时间线；终态摘要卡 + 交付/失败/停止视图。
 *
 * REQ-20261010-182114 沿袭：
 * - 写作域思考态走 ThinkingTimeline（作为状态卡的展开内容）
 * - asking/终态保留思考时间线可回看（DEC-003/007）
 *
 * REQ-20261018-235653（FR-003/FR-007/FR-008）：
 * - 行消息（message.rows）走 RowTimeline——思考/正文/工具/子代理按行序交错；
 * - 旧消息（无 rows）维持 ThinkingTimeline（双轨）；
 * - HistoryFolded 摘要卡退役：历史消息与最新消息同形态（状态卡 + 可展开时间线）。
 */
import type { ChatMessage, ExecutionPhase } from "@/lib/types";
import type { StageFlow } from "@/lib/stage";
import { derivePhaseFromMessage } from "@/lib/execution-phase";
import { BootingView } from "./execution/BootingView";
import { ThinkingTimeline } from "./execution/ThinkingTimeline";
import { RowTimeline } from "./execution/RowTimeline";
import { DeliveryCeremony } from "./execution/DeliveryCeremony";
import { FailedView } from "./execution/FailedView";
import { StoppedView } from "./execution/StoppedView";
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

  // 行消息（FR-003）：末个正文块外置常显（对齐 ZCode 的 visibleAssistantText——
  // 状态卡收起时最终回答仍可见，不随工作过程折进卡里）；其余行进卡内时间线。
  const rowList = message.rows ?? [];
  const isRowMessage = rowList.length > 0;
  const lastTextIndex = rowList.findLastIndex((row) => row.kind === "text" && !!row.text);
  const cardRows = isRowMessage && lastTextIndex >= 0
    ? rowList.filter((_, i) => i !== lastTextIndex)
    : rowList;
  const finalTextRow = isRowMessage && lastTextIndex >= 0 ? rowList[lastTextIndex] : null;

  // 时间线选路（FR-003/FR-007）：行消息走行级交错时间线，旧消息走思考时间线；
  // 终态/中断均保留可回看（DEC-003/007）
  const timeline = isRowMessage ? (
    <RowTimeline rows={cardRows} loading={loading} />
  ) : message.thoughts?.length ? (
    <ThinkingTimeline message={message} loading={loading} />
  ) : null;

  // 行消息状态卡行为（FR-003/FR-004）：运行中默认展开实时生长；提问中断（含恢复态）不收拢
  const rowCardProps = isRowMessage
    ? { defaultOpen: loading || !!message.awaitingInput, keepOpenOnEnd: !!message.awaitingInput }
    : {};

  switch (phase) {
    case "booting":
      // FR-002：reasoning/行先于首个 tool_call 到达时直接进时间线，不停留在黑屏期
      return timeline ?? <BootingView />;
    case "thinking":
      return (
        <>
          <ExecutionStatusCard message={message} loading={loading} {...rowCardProps}>
            {timeline ?? <ThinkingTimeline message={message} loading={loading} />}
          </ExecutionStatusCard>
          {finalTextRow ? <RowTimeline rows={[finalTextRow]} loading={loading} /> : null}
        </>
      );
    case "asking":
      // asking 态的 HITL UI 由 ChatPanel 外层渲染（InterviewOptions/ReviewCard）
      // 状态卡保留思考时间线到中断点（DEC-007）
      return (
        <>
          <ExecutionStatusCard message={message} summary="已暂停 · 等你回答" {...rowCardProps}>
            {timeline}
          </ExecutionStatusCard>
          {finalTextRow ? <RowTimeline rows={[finalTextRow]} /> : null}
        </>
      );
    case "answered":
      // 拍板已提交（resume 定格）：本轮以用户决策收尾——时间线保留可回看，
      // 提问已沉淀为正文行常显（finalTextRow），续跑输出归新一轮消息
      return (
        <>
          <ExecutionStatusCard message={message} summary="✓ 已拍板" tone="completed" {...rowCardProps}>
            {timeline}
          </ExecutionStatusCard>
          {finalTextRow ? <RowTimeline rows={[finalTextRow]} /> : null}
        </>
      );
    case "delivering":
      return (
        <>
          <ExecutionStatusCard message={message} summary={completedSummary(stageFlow)} tone="completed" {...rowCardProps}>
            {timeline}
          </ExecutionStatusCard>
          {finalTextRow ? <RowTimeline rows={[finalTextRow]} /> : null}
          <DeliveryCeremony message={message} stageFlow={stageFlow} />
        </>
      );
    case "failed":
      return (
        <>
          <ExecutionStatusCard message={message} summary="✗ 没能完成" tone="failed" {...rowCardProps}>
            {timeline}
          </ExecutionStatusCard>
          {finalTextRow ? <RowTimeline rows={[finalTextRow]} /> : null}
          <FailedView message={message} onRetry={onRetry} />
        </>
      );
    case "stopped":
      return (
        <>
          <ExecutionStatusCard message={message} summary="已停下" tone="completed" {...rowCardProps}>
            {timeline}
          </ExecutionStatusCard>
          {finalTextRow ? <RowTimeline rows={[finalTextRow]} /> : null}
          <StoppedView message={message} onRetry={onRetry} />
        </>
      );
    default:
      return null;
  }
}
