import type { PipelineStatus } from "../../lib/types";
import {
  PIPELINE_STEPS,
  REWORK_STAGE_LABEL,
  activeStepIndex,
  isStepDone,
  phaseDisplayName,
} from "../../lib/pipeline-steps";

/**
 * 大纲面板阶段条（REQ-20261012-202540 FR-002，DEC-002/008）——
 * 七步进度横条 + 当前停点 + 修订徽标，只在大纲面板渲染；
 * 对话侧的进度展示已迁入右侧作品栏（compact 模式随迁移移除）。
 */
type PipelineStageBarProps = {
  pipeline: PipelineStatus | null;
  /** outline 产物相对路径集合（步骤完成度推导） */
  fileRels: string[];
};

export function PipelineStageBar({ pipeline, fileRels }: PipelineStageBarProps) {
  if (!pipeline || pipeline.kind !== "pipeline") {
    if (pipeline?.kind === "legacy") {
      return (
        <div className="pipeline-stage-bar legacy">
          <span className="pipeline-phase legacy">旧版工作区</span>
          <span className="pipeline-hint">五件套修订模式</span>
        </div>
      );
    }
    return null;
  }
  const active = activeStepIndex(pipeline, fileRels);
  const phaseLabel = phaseDisplayName(pipeline.phase);
  const awaitingCount = Object.keys(pipeline.awaiting ?? {}).length;

  return (
    <div className="pipeline-stage-bar" role="status" aria-label="创作进度">
      <span className={`pipeline-phase ${pipeline.phase}`}>{phaseLabel}</span>
      <ol className="pipeline-steps">
        {PIPELINE_STEPS.map((s) => {
          const isDone = isStepDone(s.n, fileRels);
          const isActive = s.n === active;
          return (
            <li
              key={s.n}
              className={`pipeline-step${isDone ? " done" : ""}${isActive ? " active" : ""}`}
              title={`第 ${s.n} 步·${s.label}——${s.hint}`}
            >
              <span className="pipeline-step-n">{s.n}</span>
              <span className="pipeline-step-label">{s.label}</span>
            </li>
          );
        })}
      </ol>
      {pipeline.rework ? (
        <span className="pipeline-rework" title={`工单 ${pipeline.rework.ticket}`}>
          ⟲ {REWORK_STAGE_LABEL[pipeline.rework.stage] ?? pipeline.rework.stage}
        </span>
      ) : null}
      {awaitingCount > 0 ? (
        <span className="pipeline-awaiting">⏸ 等你拍板</span>
      ) : (
        <span className="pipeline-hint">接下来写：{pipeline.next_unit}</span>
      )}
    </div>
  );
}
