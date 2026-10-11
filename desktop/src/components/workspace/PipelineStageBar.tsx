import type { PipelineStatus } from "../../lib/types";

/**
 * 管线阶段条（REQ-20261011-142529 FR-010，DEC-006/014）——
 * 工业七步 0-7 进度 + 当前停点 + 回炉徽标。
 *
 * 七步完成度由产物文件推导（目录即进度，FR-001）：
 * 0-3 看文件存在；4 看 04-总纲；5 看 05-卷纲；6 看 06-章纲批数；
 * 7 永远可循环（卷末盘点），不显示终态。
 */
const STEPS = [
  { n: 0, label: "赛道" },
  { n: 1, label: "设计原则" },
  { n: 2, label: "冲突引擎" },
  { n: 3, label: "力量体系" },
  { n: 4, label: "总纲" },
  { n: 5, label: "卷纲" },
  { n: 6, label: "章纲" },
  { n: 7, label: "卷末盘点" },
];

const REWORK_STAGE_LABEL: Record<string, string> = {
  awaiting_approval: "提案待拍板",
  design_revising: "设计端修订中",
  impact_revising: "波及修订中",
};

type PipelineStageBarProps = {
  pipeline: PipelineStatus | null;
  /** outline 产物相对路径集合（步骤完成度推导） */
  fileRels: string[];
  /** 紧凑模式（嵌入 ChatPanel 顶部） */
  compact?: boolean;
};

export function PipelineStageBar({ pipeline, fileRels, compact = false }: PipelineStageBarProps) {
  if (!pipeline || pipeline.kind !== "pipeline") {
    if (pipeline?.kind === "legacy") {
      return (
        <div className={`pipeline-stage-bar legacy${compact ? " compact" : ""}`}>
          <span className="pipeline-phase legacy">旧版工作区</span>
          <span className="pipeline-hint">五件套修订模式，不走七步管线</span>
        </div>
      );
    }
    return null;
  }
  const rels = new Set(fileRels);
  const done = (n: number): boolean => {
    switch (n) {
      case 0: return rels.has("outline/00-赛道.md");
      case 1: return rels.has("outline/01-设计原则.md");
      case 2: return rels.has("outline/02-冲突引擎.md");
      case 3: return rels.has("outline/03-力量体系.md");
      case 4: return rels.has("outline/04-总纲.md");
      case 5: return [...rels].some((r) => r.startsWith("outline/05-卷纲/"));
      case 6: return [...rels].some((r) => r.startsWith("outline/06-章纲/"));
      default: return false;
    }
  };
  const phaseLabel = pipeline.phase === "storydesign" ? "设计端" : pipeline.phase === "storystructure" ? "结构端" : pipeline.phase;
  const awaitingCount = Object.keys(pipeline.awaiting ?? {}).length;

  return (
    <div className={`pipeline-stage-bar${compact ? " compact" : ""}`} role="status" aria-label="管线阶段">
      <span className={`pipeline-phase ${pipeline.phase}`}>{phaseLabel}</span>
      <ol className="pipeline-steps">
        {STEPS.map((s) => {
          const isDone = done(s.n);
          const isActive = !isDone && pipeline.next_unit.includes(s.label);
          return (
            <li
              key={s.n}
              className={`pipeline-step${isDone ? " done" : ""}${isActive ? " active" : ""}`}
              title={`第 ${s.n} 步·${s.label}`}
            >
              <span className="pipeline-step-n">{s.n}</span>
              <span className="pipeline-step-label">{s.label}</span>
            </li>
          );
        })}
      </ol>
      {pipeline.rework ? (
        <span className="pipeline-rework" title={`工单 ${pipeline.rework.ticket}`}>
          🔁 回炉·{REWORK_STAGE_LABEL[pipeline.rework.stage] ?? pipeline.rework.stage}
        </span>
      ) : null}
      {awaitingCount > 0 ? (
        <span className="pipeline-awaiting">⏸ 等你拍板</span>
      ) : (
        <span className="pipeline-hint">下一单元：{pipeline.next_unit}</span>
      )}
    </div>
  );
}
