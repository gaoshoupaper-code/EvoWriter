/**
 * 五段制阶段共享模块（REQ-20261018-115508 FR-011）——
 * 作品侧栏/产物面板阶段条与作品浮窗进度区共用：步骤定义、完成度推导、相位文案。
 *
 * 完成度由产物文件推导（目录即进度）：题材看主题.md；总纲看总纲.md；
 * 卷纲看至少一份卷纲；章纲看至少一份章纲；正文看至少一份正文。
 *
 * 术语（REQ-20261018-175526 FR-013/DEC-005）：用户可见文案统一「主题」，
 * 后端契约标签仍是「题材」（contracts STAGE_LABELS）——label 管显示、
 * match 管与后端 next_unit 的匹配，两不相扰。
 */
import type { PipelineStatus } from "./types";

export const PIPELINE_STEPS = [
  { n: 0, phase: "theme", label: "主题", match: "题材", hint: "核心爽点、核心矛盾、爽点剧情" },
  { n: 1, phase: "outline", label: "总纲", match: "总纲", hint: "核心纲一张表、主角与各卷 BOSS" },
  { n: 2, phase: "volume", label: "卷纲", match: "卷纲", hint: "卷末大爽点、发展阶段、爽点节奏" },
  { n: 3, phase: "chapter", label: "章纲", match: "章纲", hint: "逐章指令卡、伏笔登记" },
  { n: 4, phase: "text", label: "正文", match: "正文", hint: "逐章起草、自检、人修定稿" },
] as const;

export type StagePhase = (typeof PIPELINE_STEPS)[number]["phase"];

/** 写作技能开放阶段（REQ-20261025-205943 DEC-001）：总纲/卷纲/章纲/正文，题材段不开放 */
export const SKILL_STAGES: readonly { phase: "outline" | "volume" | "chapter" | "text"; label: string }[] = [
  { phase: "outline", label: "总纲" },
  { phase: "volume", label: "卷纲" },
  { phase: "chapter", label: "章纲" },
  { phase: "text", label: "正文" },
];

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

/** 活跃步：next_unit 含步骤契约标签且该步未完成；找不到返回 -1（无高亮） */
export function activeStepIndex(pipeline: PipelineStatus, fileRels: string[]): number {
  return PIPELINE_STEPS.findIndex(
    (s) => !isStepDone(s.n, fileRels) && pipeline.next_unit.includes(s.match),
  );
}

export function phaseDisplayName(phase: string): string {
  if (phase === "theme") return "定主题";
  if (phase === "outline") return "定总纲";
  if (phase === "volume") return "定卷纲";
  if (phase === "chapter") return "定章纲";
  if (phase === "text") return "写正文";
  return phase;
}

/** 阶段显示名（FR-013）：五段映射「主题…正文」；非五段阶段返回 null（调用方兜底）。 */
export function stageLabel(phase: string): string | null {
  const step = PIPELINE_STEPS.find((s) => s.phase === phase);
  return step ? step.label : null;
}

/** 后端单元标签显示映射：契约「题材」显示为「主题」，其余原样（含 legacy 文案）。 */
export function displayUnitLabel(unit: string): string {
  return unit === "题材" ? "主题" : unit;
}

/** 阶段引导语（FR-003/DEC-008）：新会话空页面的起点指引。 */
export interface StageGuideContent {
  text: string;
  suggestions: string[];
}

const STAGE_GUIDES: Record<string, StageGuideContent> = {
  theme: {
    text: "说说你想写一个什么样的故事",
    suggestions: ["我想写一个都市逆袭的故事", "我只有一个模糊想法，帮我聊聊"],
  },
  outline: {
    text: "主题已定，开始搭故事骨架",
    suggestions: ["基于主题开始设计总纲", "先聊聊主角和核心矛盾"],
  },
  volume: {
    text: "把故事拆成几卷，定每卷的大爽点",
    suggestions: ["开始设计下一卷卷纲", "聊聊全书分几卷"],
  },
  chapter: {
    text: "逐章拆细纲，给每一章写指令卡",
    suggestions: ["开始拆下一章细纲", "补一下细纲库存"],
  },
  text: {
    text: "按章纲逐章起草正文",
    suggestions: ["开始写下一章", "重写某一章"],
  },
};

/** 取阶段引导语；非五段阶段返回 null（调用方回退通用引导语）。 */
export function stageGuide(phase: string): StageGuideContent | null {
  return STAGE_GUIDES[phase] ?? null;
}
