/**
 * 七步法阶段共享模块（REQ-20261012-202540 FR-002/FR-007，DEC-008 命名清单）——
 * 作品栏进度区与大纲面板阶段条共用：步骤定义、完成度推导、徽标文案。
 *
 * 完成度由产物文件推导（目录即进度）：0-4 看单文件存在；5/6 看目录批数；
 * 7 卷末盘点永远可循环，不显示终态。
 */
import type { PipelineStatus } from "./types";

export const PIPELINE_STEPS = [
  { n: 0, label: "赛道", hint: "定类型：写什么题材、给谁看" },
  { n: 1, label: "设计原则", hint: "定基调：全书底层规则与风格" },
  { n: 2, label: "冲突引擎", hint: "定矛盾：核心对抗与张力来源" },
  { n: 3, label: "力量体系", hint: "定规则：等级、能力与成长" },
  { n: 4, label: "总纲", hint: "全书故事框架" },
  { n: 5, label: "卷纲", hint: "每卷的展开安排" },
  { n: 6, label: "章纲", hint: "逐章细纲" },
  { n: 7, label: "卷末盘点", hint: "一卷收尾：查漏与校准" },
] as const;

/** 步骤完成判定：目录即进度 */
export function isStepDone(n: number, fileRels: Iterable<string>): boolean {
  const rels = new Set(fileRels);
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
}

/** 活跃步：next_unit 含步骤名且该步未完成；找不到返回 -1（无高亮） */
export function activeStepIndex(pipeline: PipelineStatus, fileRels: string[]): number {
  return PIPELINE_STEPS.findIndex(
    (s) => !isStepDone(s.n, fileRels) && pipeline.next_unit.includes(s.label),
  );
}

export function phaseDisplayName(phase: string): string {
  return phase === "storydesign" ? "设计阶段" : phase === "storystructure" ? "结构阶段" : phase;
}

export const REWORK_STAGE_LABEL: Record<string, string> = {
  awaiting_approval: "修订提案·等你选",
  design_revising: "修订中·设计部分",
  impact_revising: "修订中·牵连前文",
};
