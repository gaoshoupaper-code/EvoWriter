/**
 * HistoryFolded —— 历史消息折叠态（只留结果+可展开执行过程摘要）
 *
 * 触发：切到非当前活跃的 message（历史 message）。
 * 历史 message 只显示最终正文 + 可展开"执行过程"折叠区。
 *
 * REQ-20261010-182114（FR-006）：展开区内渲染思考时间线（DEC-003
 * 历史消息同样可点开回看），stageFlow 阶段轨迹保留在其后。
 */
import { useState } from "react";
import type { ChatMessage } from "@/lib/types";
import type { StageFlow } from "@/lib/stage";
import { getStageDisplayName } from "@/lib/yan-copy";
import { ThinkingTimeline } from "./ThinkingTimeline";

interface HistoryFoldedProps {
  stageFlow: StageFlow | null;
  message?: ChatMessage;
}

function formatDuration(ms: number | null | undefined): string | null {
  if (ms == null) return null;
  const sec = ms / 1000;
  return sec < 60 ? `${sec.toFixed(0)}s` : `${Math.floor(sec / 60)}m${Math.round(sec % 60)}s`;
}

export function HistoryFolded({ stageFlow, message }: HistoryFoldedProps) {
  const [expanded, setExpanded] = useState(false);

  const hasThoughts = !!message?.thoughts?.length;
  const hasStageFlow = !!stageFlow && stageFlow.stages.length > 0;
  if (!hasThoughts && !hasStageFlow) return null;

  const totalDuration = formatDuration(stageFlow?.totalDurationMs);

  return (
    <div className="yan-history" data-phase="history">
      <button
        type="button"
        className="yan-history-toggle"
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? "▴ 收起执行过程" : "▾ 查看执行过程"}
      </button>

      {expanded ? (
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
      ) : null}
    </div>
  );
}
