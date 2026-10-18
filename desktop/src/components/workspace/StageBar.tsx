/**
 * StageBar —— 对话区顶部阶段条（REQ-20261018-175526 FR-001/002/007/010，DEC-001）。
 *
 * 五段制工作区：横排「主题·总纲·卷纲·章纲·正文」，当前阶段高亮；点非当前
 * 阶段 = 只读查看该阶段历史（不创建会话）；等拍板时当前阶段带「等你拍板」。
 * 用户可见范围内不存在任何手动会话操作（FR-001），「重新开始」是唯一显式
 * 会话动作（重开逃生门，FR-007，生成中置灰）。
 *
 * 非 five 工作区：图片域显示「图片创作」、legacy 显示「旧版工作区」占位标签，
 * 无阶段项与历史入口（历史按阶段归组，非五段制不适用）。
 */
import { useState } from "react";
import { useContentStore } from "@/stores/content";
import { useExecutionStore } from "@/stores/execution";
import { useWorkspaceStore } from "@/stores/workspace";
import { PIPELINE_STEPS } from "@/lib/pipeline-steps";

type StageBarProps = {
  onOpenStageHistory: (stage: string) => void;
  onRestart: () => void | Promise<void>;
};

export function StageBar({ onOpenStageHistory, onRestart }: StageBarProps) {
  const pipeline = useContentStore((s) => s.outlinePipeline);
  const domain = useWorkspaceStore((s) => s.activeWorkspaceDomain);
  const loading = useExecutionStore((s) => s.loading);
  const [restartBusy, setRestartBusy] = useState(false);

  const isFive = pipeline?.kind === "five";
  const awaiting = isFive && Object.keys(pipeline.awaiting ?? {}).length > 0;
  const phase = isFive ? pipeline.phase : "";
  // FR-002 失败语义：写作域管线未就绪显示中性占位（bootstrap 期间/拉取失败），
  // 不误标「图片创作」；图片域与 legacy 各自占位
  const placeholder =
    pipeline?.kind === "legacy"
      ? "旧版工作区"
      : domain === "image"
        ? "图片创作"
        : "准备中…";

  async function handleRestart() {
    if (loading || restartBusy) return;
    setRestartBusy(true);
    try {
      await onRestart();
    } finally {
      setRestartBusy(false);
    }
  }

  return (
    <div className="stage-bar" role="navigation" aria-label="创作阶段">
      {isFive ? (
        <ol className="stage-bar-steps">
          {PIPELINE_STEPS.map((step) => {
            const isCurrent = step.phase === phase;
            return (
              <li key={step.phase} className={`stage-bar-step${isCurrent ? " current" : ""}`}>
                <button
                  type="button"
                  className="stage-bar-name"
                  disabled={isCurrent}
                  onClick={() => onOpenStageHistory(step.phase)}
                  title={isCurrent ? "当前阶段" : `查看${step.label}阶段的历史对话`}
                >
                  {step.label}
                </button>
                {isCurrent && awaiting ? <span className="stage-bar-awaiting">等你拍板</span> : null}
              </li>
            );
          })}
        </ol>
      ) : (
        <span className="stage-bar-placeholder">{placeholder}</span>
      )}
      <button
        type="button"
        className="stage-bar-restart"
        onClick={() => void handleRestart()}
        disabled={loading || restartBusy}
        title={loading ? "生成中，不能重新开始" : "清空重来：归档当前对话，开一个新的本阶段对话"}
      >
        {restartBusy ? "重开中…" : "重新开始"}
      </button>
    </div>
  );
}
