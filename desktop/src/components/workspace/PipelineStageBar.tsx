import { useEffect, useRef, useState } from "react";
import type { PipelineStatus } from "../../lib/types";
import {
  PIPELINE_STEPS,
  activeStepIndex,
  displayUnitLabel,
  isStepDone,
  phaseDisplayName,
} from "../../lib/pipeline-steps";

/**
 * 产物面板阶段条（REQ-20261018-115508 FR-011）——
 * 五段进度横条 + 当前停点；对话侧的进度展示在右侧作品浮窗（DEC-012）。
 *
 * 段间切换人工闸（REQ-20261019-232617 FR-004，DEC-008）：已完成的段可点击
 * 跳段/回退（点哪回哪）——两段式确认防误触：首次点击亮「回X？」，
 * 3 秒内再点才回调 onStageJump；active 段不可点（已在当前段）。
 */
type PipelineStageBarProps = {
  pipeline: PipelineStatus | null;
  /** 五段产物相对路径集合（步骤完成度推导） */
  fileRels: string[];
  /** 跳段/回退回调（人工段指针写入由调用方完成）；缺省时已完成段不可点 */
  onStageJump?: (stage: string) => void;
};

/** 两段式确认的冷却毫秒数 */
const ARM_RESET_MS = 3000;

export function PipelineStageBar({ pipeline, fileRels, onStageJump }: PipelineStageBarProps) {
  const [armedStep, setArmedStep] = useState<number | null>(null);
  const armTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (armTimer.current) clearTimeout(armTimer.current);
  }, []);

  if (!pipeline || pipeline.kind !== "five") {
    if (pipeline?.kind === "legacy") {
      return (
        <div className="pipeline-stage-bar legacy">
          <span className="pipeline-phase legacy">旧版工作区</span>
          <span className="pipeline-hint">只读模式</span>
        </div>
      );
    }
    return null;
  }
  const active = activeStepIndex(pipeline, fileRels);
  const phaseLabel = phaseDisplayName(pipeline.phase);
  const awaitingCount = Object.keys(pipeline.awaiting ?? {}).length;
  const readyNext = pipeline.ready?.ready === true ? pipeline.ready.next : null;

  function handleStepClick(n: number, phase: string) {
    if (armedStep === n) {
      if (armTimer.current) clearTimeout(armTimer.current);
      setArmedStep(null);
      onStageJump?.(phase);
      return;
    }
    setArmedStep(n);
    if (armTimer.current) clearTimeout(armTimer.current);
    armTimer.current = setTimeout(() => setArmedStep(null), ARM_RESET_MS);
  }

  return (
    <div className="pipeline-stage-bar" role="status" aria-label="创作进度">
      <span className={`pipeline-phase ${pipeline.phase}`}>{phaseLabel}</span>
      <ol className="pipeline-steps">
        {PIPELINE_STEPS.map((s) => {
          const isDone = isStepDone(s.n, fileRels);
          const isActive = s.n === active;
          const jumpable = Boolean(onStageJump) && isDone && !isActive;
          const armed = armedStep === s.n;
          const hint = jumpable
            ? armed
              ? `再点一次确认回到「${s.label}」`
              : `回到${s.label}（段间切换需确认）`
            : `第 ${s.n + 1} 步·${s.label}——${s.hint}`;
          return (
            <li
              key={s.n}
              className={`pipeline-step${isDone ? " done" : ""}${isActive ? " active" : ""}${jumpable ? " jumpable" : ""}${armed ? " armed" : ""}`}
              title={hint}
            >
              {jumpable ? (
                <button
                  type="button"
                  className="pipeline-step-jump"
                  aria-label={hint}
                  onClick={() => handleStepClick(s.n, s.phase)}
                >
                  <span className="pipeline-step-n">{s.n + 1}</span>
                  <span className="pipeline-step-label">{armed ? `回${s.label}？` : s.label}</span>
                </button>
              ) : (
                <>
                  <span className="pipeline-step-n">{s.n + 1}</span>
                  <span className="pipeline-step-label">{s.label}</span>
                </>
              )}
            </li>
          );
        })}
      </ol>
      {awaitingCount > 0 ? (
        <span className="pipeline-awaiting">⏸ 等你拍板</span>
      ) : readyNext ? (
        <span className="pipeline-ready">✅ 已齐全，可进入下一步</span>
      ) : (
        <span className="pipeline-hint">接下来写：{displayUnitLabel(pipeline.next_unit)}</span>
      )}
    </div>
  );
}
