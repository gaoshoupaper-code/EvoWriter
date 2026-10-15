/**
 * 三步制阶段共享模块（REQ-20261015-161200 FR-008）——
 * 作品栏进度区与大纲面板阶段条共用：步骤定义、完成度推导、相位文案。
 *
 * 完成度由产物文件推导（目录即进度）：第 0 步看世界观单文件；第 1 步看
 * 总纲 + 至少一份卷纲；第 2 步看至少一份章纲批。
 */
import type { PipelineStatus } from "./types";

export const PIPELINE_STEPS = [
  { n: 0, label: "世界观", hint: "核心冲突、力量体系、金手指、大法则" },
  { n: 1, label: "总纲卷纲", hint: "分卷地图、主角与 BOSS、每卷期待与爽点" },
  { n: 2, label: "章纲", hint: "逐批剧情走向、埋钩子回收钩子" },
] as const;

/** 步骤完成判定：目录即进度 */
export function isStepDone(n: number, fileRels: Iterable<string>): boolean {
  const rels = new Set(fileRels);
  switch (n) {
    case 0: return rels.has("outline/世界观.md");
    case 1: return rels.has("outline/总纲.md") && [...rels].some((r) => /^outline\/卷纲-第\d+卷\.md$/.test(r));
    case 2: return [...rels].some((r) => /^outline\/章纲-卷\d+批\d+\.md$/.test(r));
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
  if (phase === "worldview") return "定世界观";
  if (phase === "outline") return "定总纲卷纲";
  if (phase === "chapters") return "定章纲";
  return phase;
}
