/**
 * 需求表单 → demand.md 模板渲染（FR-002 / DEC-013，REQ-20260920-150149；
 * 双 agent 管线改造 REQ-20261011-142529 FR-009/DEC-013——赛道字段 + 新泳道元信息）。
 *
 * 字段集：必填三项（题材/类型、核心创意/一句话卖点、主角与核心设定要点）
 * + 赛道两项（平台/品类，默认番茄/玄幻——网文连载为主战场 DEC-013）
 * + 选填两项（大纲侧重、风格偏好）。结构对齐 harness 包
 * prompts/demand_template.md 的四层框架。
 */

export type DemandFields = {
  /** 赛道：平台（默认番茄——体检参数随赛道，FR-009） */
  platform: string;
  /** 赛道：品类（默认玄幻） */
  category: string;
  /** 必填：题材/类型（如：玄幻·热血升级流） */
  genre: string;
  /** 必填：核心创意/一句话卖点（logline + 爽点钩子） */
  premise: string;
  /** 必填：主角与核心设定要点（身份起点、欲望弱点、金手指边界） */
  protagonist: string;
  /** 选填：大纲侧重（如：重人物弧光 / 重世界观 / 重主线冲突） */
  focus: string;
  /** 选填：风格偏好（基调、节奏、禁忌红线等自由文本） */
  stylePrefs: string;
};

export const DEMAND_DEFAULTS: Pick<DemandFields, "platform" | "category"> = {
  platform: "番茄",
  category: "玄幻",
};

export const DEMAND_REQUIRED_FIELDS: Array<keyof DemandFields> = ["genre", "premise", "protagonist"];

export function isDemandValid(fields: DemandFields): boolean {
  return DEMAND_REQUIRED_FIELDS.every((key) => fields[key].trim().length > 0);
}

/** 旧记忆字段（无赛道键）合并默认值——记忆隔离，不覆盖默认赛道。 */
export function normalizeDemandFields(saved: Partial<DemandFields> | null | undefined): DemandFields {
  const base: DemandFields = {
    platform: (saved?.platform ?? "").trim() || DEMAND_DEFAULTS.platform,
    category: (saved?.category ?? "").trim() || DEMAND_DEFAULTS.category,
    genre: saved?.genre ?? "",
    premise: saved?.premise ?? "",
    protagonist: saved?.protagonist ?? "",
    focus: saved?.focus ?? "",
    stylePrefs: saved?.stylePrefs ?? "",
  };
  return base;
}

/** 表单字段 → demand.md 全文（执行端原样写入 workspace/demand.md）。 */
export function renderDemandMd(fields: DemandFields, workspaceTitle?: string): string {
  const now = new Date().toISOString();
  const title = workspaceTitle?.trim() || "未命名大纲";

  const focusSection = fields.focus.trim()
    ? `\n## 大纲侧重（选填）\n\n- **侧重说明**：${fields.focus.trim()}\n`
    : "";
  const styleSection = fields.stylePrefs.trim()
    ? `\n## 风格层（选填）\n\n- **基调与偏好**：${fields.stylePrefs.trim()}\n`
    : "";

  return `<!--
元信息（程序可解析；表单直入，REQ-20261011-142529 双 agent 管线）：
- mode: interactive（一轮一个产出单元、拍板后落盘；严禁自动连跑）
- status: confirmed
- updated: ${now}
- structural_expectations: 期望双 agent 管线执行（expected_subagents:
  ["storydesign", "storystructure"]；review_required: false——review 泳道延后）
- receipt_skip: false
-->

# 创作需求文档（${title}）

## 核心层

- **赛道**：
  - 平台：${(fields.platform ?? "").trim() || DEMAND_DEFAULTS.platform}
  - 品类：${(fields.category ?? "").trim() || DEMAND_DEFAULTS.category}
- **体裁类型**：${fields.genre.trim()}
- **核心创意与卖点**：
  - 一句话故事（logline）：${fields.premise.trim()}
  - 核心卖点 / 爽点钩子：${fields.premise.trim()}

## 设定层

- **主角与核心设定要点**：
  - 主角设定：${fields.protagonist.trim()}
  - 金手指 / 核心设定边界：${fields.protagonist.trim()}
${focusSection}${styleSection}
## 留白说明

未填写的维度（世界观细节、配角关系网、篇幅档位等）由设计端按赛道惯例补齐，
经单元拍板确认后落盘。
`;
}

/** 提交后在对话里展示的用户消息摘要（简短，不倒整个表单）。 */
export function demandSummary(fields: DemandFields): string {
  return `【创作需求】${fields.category.trim()}·${fields.genre.trim()}（${fields.platform.trim()}） —— ${fields.premise.trim().slice(0, 80)}${fields.premise.trim().length > 80 ? "…" : ""}`;
}

/** 发给设计端的 kickoff 指令（demand.md 已由执行端写入 workspace）。
 * 双管线（REQ-20261011-142529 FR-003）：从第 0 步赛道确认开始，一轮一个单元。 */
export const DEMAND_KICKOFF_PROMPT =
  "请阅读 demand.md 中的创作需求，从第 0 步「赛道确认」开始按系统提示词的单元流程推进：" +
  "起草 → confirm_with_user 拍板 → 批准后落盘 → 停下等我下一条消息。严禁自动连跑。";

/** 「跳过澄清」入口发出的 resume 指令——与拍板闸的跳过口径对齐（DEC-004）。 */
export const DEMAND_SKIP_CLARIFICATION_PROMPT = "不用问了，直接写。";
