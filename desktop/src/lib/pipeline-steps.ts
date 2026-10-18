/**
 * 五段制阶段共享模块（REQ-20261018-115508 FR-011）——
 * 作品侧栏/产物面板阶段条与作品浮窗进度区共用：步骤定义、完成度推导、相位文案。
 *
 * 完成度由产物文件推导（目录即进度）：题材看主题.md；总纲看总纲.md；
 * 卷纲看至少一份卷纲；章纲看至少一份章纲；正文看至少一份正文。
 */
import type { PipelineStatus } from "./types";

export const PIPELINE_STEPS = [
  { n: 0, label: "题材", hint: "核心爽点、核心矛盾、爽点剧情" },
  { n: 1, label: "总纲", hint: "核心纲一张表、主角与各卷 BOSS" },
  { n: 2, label: "卷纲", hint: "卷末大爽点、发展阶段、爽点节奏" },
  { n: 3, label: "章纲", hint: "逐章指令卡、伏笔登记" },
  { n: 4, label: "正文", hint: "逐章起草、自检、人修定稿" },
] as const;

/** 步骤完成判定：目录即进度 */
export function isStepDone(n: number, fileRels: Iterable<string>): boolean {
  const rels = new Set(fileRels);
  switch (n) {
    case 0: return rels.has("主题.md");
    case 1: return rels.has("总纲.md");
    case 2: return [...rels].some((r) => /^卷纲\/第\d+卷\.md$/.test(r));
    case 3: return [...rels].some((r) => /^章纲\/第\d+章\.md$/.test(r));
    case 4: return [...rels].some((r) => /^正文\/第\d+章.*\.md$/.test(r));
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
  if (phase === "theme") return "定题材";
  if (phase === "outline") return "定总纲";
  if (phase === "volume") return "定卷纲";
  if (phase === "chapter") return "定章纲";
  if (phase === "text") return "写正文";
  return phase;
}
