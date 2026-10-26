/**
 * ModeToggle —— 创作双模式开关（REQ-20261026-191719 FR-008 / DEC-008）。
 *
 * 输入框旁单开关，显示并切换「当前所在段」的模式（zcode 权限切换形态）：
 * - 逐步确认（interactive）：现状——agent 每步停下问用户
 * - 自动完成（auto）：段内不提问一路干完，只在停等点（批次/文风/段审）停下
 *
 * 各段独立记忆（服务端 stage_modes）；题材/总纲段锁死逐步（DEC-002），开关
 * 置灰。切换在下一停等边界生效（DEC-016）：正在跑的 run 不受影响。
 */
import { Bot, MessagesSquare } from "lucide-react";
import { useContentStore } from "@/stores/content";
import { useStageSessionStore } from "@/stores/stage-session";
import { useWorkspaceStore } from "@/stores/workspace";
import { stageLabel } from "@/lib/pipeline-steps";

export function ModeToggle() {
  const pipeline = useContentStore((s) => s.outlinePipeline);
  const stageModes = useStageSessionStore((s) => s.stageModes);
  const autoEligible = useStageSessionStore((s) => s.autoEligibleStages);
  const toggleStageMode = useStageSessionStore((s) => s.toggleStageMode);
  const workspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);

  if (!pipeline || pipeline.kind !== "five" || !pipeline.phase) return null;

  const stage = pipeline.phase;
  const eligible = autoEligible.includes(stage);
  const mode = stageModes?.[stage] ?? "interactive";
  const isAuto = mode === "auto";

  if (!eligible) {
    // DEC-002：题材/总纲锁死逐步——置灰可见，title 说明
    return (
      <button
        type="button"
        className="mode-toggle locked"
        disabled
        title={`${stageLabel(stage) ?? "本段"}固定逐步确认，不支持自动模式`}
      >
        <MessagesSquare size={14} aria-hidden="true" />
        <span>逐步确认</span>
      </button>
    );
  }

  return (
    <button
      type="button"
      className={`mode-toggle${isAuto ? " auto" : ""}`}
      onClick={() => {
        if (workspaceId) void toggleStageMode(workspaceId, stage);
      }}
      title={
        isAuto
          ? "自动完成：本段不再提问，写完在停等点等你审查。点击切回逐步确认（下一停等点生效）"
          : "逐步确认：每步停下问你。点击切到自动完成（下一停等点生效）"
      }
    >
      {isAuto ? (
        <Bot size={14} aria-hidden="true" />
      ) : (
        <MessagesSquare size={14} aria-hidden="true" />
      )}
      <span>{isAuto ? "自动完成" : "逐步确认"}</span>
    </button>
  );
}
