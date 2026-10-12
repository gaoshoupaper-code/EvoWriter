/**
 * 七步法阶段共享模块测试（REQ-20261012-202540 FR-002/FR-007，DEC-008）。
 * 覆盖：完成度推导（目录即进度）、活跃步匹配、徽标文案零黑话。
 */
import { describe, expect, it } from "vitest";
import {
  PIPELINE_STEPS,
  REWORK_STAGE_LABEL,
  activeStepIndex,
  isStepDone,
  phaseDisplayName,
} from "@/lib/pipeline-steps";

const FULL_FILES = [
  "outline/00-赛道.md",
  "outline/01-设计原则.md",
  "outline/02-冲突引擎.md",
  "outline/03-力量体系.md",
  "outline/04-总纲.md",
  "outline/05-卷纲/第1卷.md",
  "outline/06-章纲/第1批.md",
];

describe("isStepDone 完成度推导", () => {
  it("0-4 步看单文件存在", () => {
    const rels = ["outline/00-赛道.md", "outline/04-总纲.md"];
    expect(isStepDone(0, rels)).toBe(true);
    expect(isStepDone(1, rels)).toBe(false);
    expect(isStepDone(4, rels)).toBe(true);
  });

  it("5/6 步看目录批（前缀匹配）", () => {
    expect(isStepDone(5, ["outline/05-卷纲/第1卷.md"])).toBe(true);
    expect(isStepDone(5, ["outline/05-卷纲.md"])).toBe(false);
    expect(isStepDone(6, ["outline/06-章纲/第1批.md"])).toBe(true);
  });

  it("第 7 步卷末盘点永远不显示终态", () => {
    expect(isStepDone(7, FULL_FILES)).toBe(false);
  });
});

describe("activeStepIndex 活跃步匹配", () => {
  it("next_unit 含步骤名且未完成 → 该步为活跃", () => {
    const pipeline = { kind: "pipeline", phase: "storystructure", next_unit: "卷纲·第1卷" } as any;
    const rels = FULL_FILES.filter((r) => !r.startsWith("outline/05-"));
    expect(activeStepIndex(pipeline, rels)).toBe(5);
  });

  it("已完成或名字不匹配 → -1（无活跃高亮）", () => {
    const pipeline = { kind: "pipeline", phase: "storystructure", next_unit: "卷纲·第1卷" } as any;
    expect(activeStepIndex(pipeline, FULL_FILES)).toBe(-1);
  });
});

describe("命名清单（DEC-008）：零黑话", () => {
  it("八步名字与副标齐全", () => {
    expect(PIPELINE_STEPS.map((s) => s.label)).toEqual([
      "赛道", "设计原则", "冲突引擎", "力量体系", "总纲", "卷纲", "章纲", "卷末盘点",
    ]);
    for (const s of PIPELINE_STEPS) expect(s.hint.length).toBeGreaterThan(0);
  });

  it("phase 显示名不含「设计端/结构端」", () => {
    expect(phaseDisplayName("storydesign")).toBe("设计阶段");
    expect(phaseDisplayName("storystructure")).toBe("结构阶段");
  });

  it("回炉文案不含「回炉」字样", () => {
    const joined = Object.values(REWORK_STAGE_LABEL).join("");
    expect(joined).not.toContain("回炉");
    expect(REWORK_STAGE_LABEL.awaiting_approval).toBe("修订提案·等你选");
  });
});
