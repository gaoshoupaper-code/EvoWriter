/**
 * DeliveryCeremony —— 交付仪式·庆祝+字数/耗时摘要
 *
 * 触发：final 事件 → message.status: completed → phase: delivering。
 * 制造成就感峰值。
 *
 * 元素：
 * - ✨ 交稿语（随机，庆祝调性）
 * - 结果摘要（产出内容/字数/耗时）
 * - 收尾互动语
 */
import { useMemo } from "react";
import type { ChatMessage } from "@/lib/types";
import type { StageFlow } from "@/lib/stage";
import { DELIVERY_COPY, DELIVERY_INTERACTION, pickRandom } from "@/lib/yan-copy";
import { useWorkrailStore } from "@/stores/workrail";
import { formatDurationZh } from "./ExecutionStatusCard";

interface DeliveryCeremonyProps {
  message: ChatMessage;
  stageFlow: StageFlow | null;
}

export function DeliveryCeremony({ message, stageFlow }: DeliveryCeremonyProps) {
  const deliveryLine = useMemo(() => pickRandom(DELIVERY_COPY).text, []);

  // 从 stageFlow 派生摘要（FR-004：v8 章数/字数展示已随细纲正文链路退役，仅保留耗时与轮次）
  const totalDuration = formatDurationZh(stageFlow?.totalDurationMs);
  const writingStage = stageFlow?.stages.find((s) => s.type === "storybuilding");
  const iterationCount = writingStage?.subSteps.length ?? null;

  return (
    <div className="yan-delivery" data-phase="delivering">
      <div className="yan-delivery-header">
        <span className="yan-delivery-sparkle">✨</span>
        <span className="yan-delivery-title">{deliveryLine}</span>
      </div>

      {(iterationCount != null || totalDuration != null) && (
        <div className="yan-delivery-summary">
          {iterationCount != null && iterationCount > 0 ? (
            <span className="yan-delivery-stat">{iterationCount} 轮构建</span>
          ) : null}
          {totalDuration != null ? (
            <span className="yan-delivery-stat">耗时 {totalDuration}</span>
          ) : null}
        </div>
      )}

      {/* FR-004/AC-006：交付即给产物入口——展开作品栏并高亮本次新增 */}
      <div className="yan-delivery-actions">
        <button
          type="button"
          className="yan-delivery-view-button"
          onClick={() => useWorkrailStore.getState().revealDelivery()}
        >
          查看新作品
        </button>
      </div>

      <p className="yan-delivery-interaction">{DELIVERY_INTERACTION}</p>
    </div>
  );
}
