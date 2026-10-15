/**
 * 需求表单 → demand.md 模板渲染（REQ-20261015-161200 FR-007，DEC-007）。
 *
 * 字段集 4 项：平台 / 品类（默认番茄/玄幻，网文连载为主战场）/
 * 一句话点子 / 金手指。填完直接进入三步流程，主控不做多维度访谈。
 */

export type DemandFields = {
  /** 赛道：平台（默认番茄） */
  platform: string;
  /** 赛道：品类（默认玄幻） */
  category: string;
  /** 必填：一句话点子（主角是谁 + 核心困境 + 独特抓手） */
  premise: string;
  /** 必填：金手指（能力与边界，含代价或限制） */
  goldenFinger: string;
};

export const DEMAND_DEFAULTS: Pick<DemandFields, "platform" | "category"> = {
  platform: "番茄",
  category: "玄幻",
};

export const DEMAND_REQUIRED_FIELDS: Array<keyof DemandFields> = ["premise", "goldenFinger"];

export function isDemandValid(fields: DemandFields): boolean {
  return DEMAND_REQUIRED_FIELDS.every((key) => fields[key].trim().length > 0);
}

/** 旧记忆字段（七步制 7 字段形态）合并默认值——记忆隔离，不覆盖默认赛道。 */
export function normalizeDemandFields(saved: Partial<DemandFields> | null | undefined): DemandFields {
  return {
    platform: (saved?.platform ?? "").trim() || DEMAND_DEFAULTS.platform,
    category: (saved?.category ?? "").trim() || DEMAND_DEFAULTS.category,
    premise: saved?.premise ?? "",
    goldenFinger: saved?.goldenFinger ?? "",
  };
}

/** 表单字段 → demand.md 全文（执行端原样写入 workspace/demand.md）。 */
export function renderDemandMd(fields: DemandFields, workspaceTitle?: string): string {
  const now = new Date().toISOString();
  const title = workspaceTitle?.trim() || "未命名大纲";

  return `<!--
元信息（程序可解析；表单直入，三步管线 REQ-20261015-161200）：
- mode: interactive（一轮一个产出单元、拍板后落盘；严禁自动连跑）
- status: confirmed
- updated: ${now}
- receipt_skip: false
-->

# 创作需求文档（${title}）

- 平台：${(fields.platform ?? "").trim() || DEMAND_DEFAULTS.platform}
- 品类：${(fields.category ?? "").trim() || DEMAND_DEFAULTS.category}
- 一句话点子：${fields.premise.trim()}
- 金手指：${fields.goldenFinger.trim()}
`;
}

/** 提交后在对话里展示的用户消息摘要（简短，不倒整个表单）。 */
export function demandSummary(fields: DemandFields): string {
  return `【创作需求】${fields.category.trim()}（${fields.platform.trim()}） —— ${fields.premise.trim().slice(0, 80)}${fields.premise.trim().length > 80 ? "…" : ""}`;
}

/** 发给主控的 kickoff 指令（demand.md 已由执行端写入 workspace）。
 * 三步管线（REQ-20261015-161200 FR-003）：从第①步定世界观开始，一轮一个单元。 */
export const DEMAND_KICKOFF_PROMPT =
  "请阅读 demand.md 中的创作需求，从第①步「定世界观」开始按主控系统提示词的单元流程推进：" +
  "委托子代理起草 → confirm_with_user 拍板 → 批准后委托落盘 → 停下等我下一条消息。严禁自动连跑。";

/** 「跳过澄清」入口发出的 resume 指令——与拍板闸的跳过口径对齐。 */
export const DEMAND_SKIP_CLARIFICATION_PROMPT = "不用问了，直接写。";
