/**
 * HistoryFolded —— 历史消息摘要卡（REQ-20261012-202540 FR-004，DEC-006 改版）
 *
 * 触发：切到非当前活跃的 assistant 消息（历史 message）。
 * 与当前消息统一为 ExecutionStatusCard 形态：默认一行「✓ 完成 · N 轮 · 耗时」，
 * 点开回看思考时间线（DEC-003 历史可回看）与阶段轨迹。
 */
import type { ChatMessage } from "@/lib/types";
import type { StageFlow } from "@/lib/stage";
import { getStageDisplayName } from "@/lib/yan-copy";
import { ThinkingTimeline } from "./ThinkingTimeline";
import { ExecutionStatusCard, formatDurationZh } from "./ExecutionStatusCard";

interface HistoryFoldedProps {
  stageFlow: StageFlow | null;
  message?: ChatMessage;
}

export function HistoryFolded({ stageFlow, message }: HistoryFoldedProps) {
  const hasThoughts = !!message?.thoughts?.length;
  const hasStageFlow = !!stageFlow && stageFlow.stages.length > 0;
  if (!hasThoughts && !hasStageFlow) return null;

  const totalDuration = formatDurationZh(stageFlow?.totalDurationMs);
  const writingStage = stageFlow?.stages.find((s) => s.type === "storybuilding");
  const rounds = writingStage?.subSteps.length ?? 0;
  const summaryParts = ["✓ 完成"];
  if (rounds > 0) summaryParts.push(`${rounds} 轮`);
  if (totalDuration) summaryParts.push(`耗时 ${totalDuration}`);
  const summary = summaryParts.join(" · ");

  return (
    <div className="yan-history" data-phase="history">
      <ExecutionStatusCard message={message ?? { role: "assistant", content: "" }} summary={summary} tone="completed">
        <div className="yan-history-detail">
          {hasThoughts ? <ThinkingTimeline message={message!} /> : null}
          {hasStageFlow ? (
            <>
              <div className="yan-history-trail">
                {stageFlow!.stages.map((stage, idx) => (
                  <span key={stage.id} className="yan-trail-item">
                    {idx > 0 ? <span className="yan-trail-sep">→</span> : null}
                    <span className={`yan-trail-mark ${stage.status === "completed" ? "completed" : "failed"}`}>
                      {stage.status === "completed" ? "✓" : "✗"}
                    </span>
                    <span className="yan-trail-label">{getStageDisplayName(stage.type)}</span>
                  </span>
                ))}
              </div>
              <div className="yan-history-stats">
                {totalDuration != null ? <span>耗时 {totalDuration}</span> : null}
              </div>
            </>
          ) : null}
        </div>
      </ExecutionStatusCard>
    </div>
  );
}
