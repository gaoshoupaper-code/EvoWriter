/**
 * 需求表单 → demand.md 模板渲染（FR-002 / DEC-013，REQ-20260920-150149）。
 *
 * 字段集：必填三项（题材/类型、核心创意/一句话卖点、主角与核心设定要点）
 * + 选填两项（大纲侧重、风格偏好）。结构对齐 harness 包 prompts/demand_template.md
 * 的四层 12 维框架——表单只填核心层/设定层主干与风格层，留白由故事专家自行创作。
 */

export type DemandFields = {
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

export const DEMAND_REQUIRED_FIELDS: Array<keyof DemandFields> = ["genre", "premise", "protagonist"];

export function isDemandValid(fields: DemandFields): boolean {
  return DEMAND_REQUIRED_FIELDS.every((key) => fields[key].trim().length > 0);
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
元信息（程序可解析；v7+ 表单直入，无访谈环节）：
- mode: auto
- status: confirmed
- updated: ${now}
- structural_expectations: 期望故事专家（storybuilding）执行大纲三件套生成，reviewer 审查
  （expected_subagents: ["storybuilding"]；review_required: true）
-->

# 创作需求文档（${title}）

## 核心层

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

未填写的维度（世界观细节、配角关系网、篇幅档位、目标配比等）由故事专家按题材惯例
自行创作补齐，保持四层 12 维框架完整。
`;
}

/** 提交后在对话里展示的用户消息摘要（简短，不倒整个表单）。 */
export function demandSummary(fields: DemandFields): string {
  return `【创作需求】${fields.genre.trim()} —— ${fields.premise.trim().slice(0, 80)}${fields.premise.trim().length > 80 ? "…" : ""}`;
}

/** 发给故事专家的 kickoff 指令（demand.md 已由执行端写入 workspace）。 */
export const DEMAND_KICKOFF_PROMPT =
  "请阅读 demand.md 中的创作需求，生成大纲三件套（storyline / character / worldview），完成后交由 reviewer 审查。";
