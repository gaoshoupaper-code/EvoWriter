/**
 * 三步制阶段共享模块测试（REQ-20261015-161200 FR-008）。
 * 覆盖：完成度推导（目录即进度）、活跃步匹配、相位文案。
 */
import { describe, expect, it } from "vitest";
import {
  PIPELINE_STEPS,
  activeStepIndex,
  isStepDone,
  phaseDisplayName,
} from "@/lib/pipeline-steps";

const FULL_FILES = [
  "outline/世界观.md",
  "outline/总纲.md",
  "outline/卷纲-第1卷.md",
  "outline/章纲-卷1批1.md",
  "outline/期待台账.md",
];

describe("isStepDone 完成度推导", () => {
  it("第 0 步看世界观单文件", () => {
    expect(isStepDone(0, ["outline/世界观.md"])).toBe(true);
    expect(isStepDone(0, ["outline/总纲.md"])).toBe(false);
  });

  it("第 1 步看总纲 + 至少一份卷纲", () => {
    expect(isStepDone(1, ["outline/总纲.md"])).toBe(false);
    expect(isStepDone(1, ["outline/总纲.md", "outline/卷纲-第1卷.md"])).toBe(true);
    expect(isStepDone(1, ["outline/总纲.md", "outline/卷纲-第12卷.md"])).toBe(true);
  });

  it("第 2 步看至少一份章纲批", () => {
    expect(isStepDone(2, ["outline/章纲-卷1批1.md"])).toBe(true);
    expect(isStepDone(2, ["outline/章纲-卷10批23.md"])).toBe(true);
    expect(isStepDone(2, ["outline/期待台账.md"])).toBe(false);
  });

  it("旧七步文件名不算三步产物", () => {
    expect(isStepDone(0, ["outline/00-赛道.md"])).toBe(false);
    expect(isStepDone(1, ["outline/04-总纲.md", "outline/05-卷纲/第1卷.md"])).toBe(false);
  });

  it("全部完成后三步齐绿", () => {
    for (let n = 0; n < 3; n++) expect(isStepDone(n, FULL_FILES)).toBe(true);
  });
});

describe("activeStepIndex 活跃步匹配", () => {
  it("next_unit 含步骤名且未完成 → 该步为活跃", () => {
    const pipeline = { kind: "pipeline", phase: "outline", next_unit: "总纲卷纲" } as any;
    const rels = ["outline/世界观.md"];
    expect(activeStepIndex(pipeline, rels)).toBe(1);
  });

  it("已完成或名字不匹配 → -1（无活跃高亮）", () => {
    const pipeline = { kind: "pipeline", phase: "chapters", next_unit: "章纲" } as any;
    expect(activeStepIndex(pipeline, FULL_FILES)).toBe(-1);
  });
});

describe("三步命名与相位文案", () => {
  it("三步名字与副标齐全", () => {
    expect(PIPELINE_STEPS.map((s) => s.label)).toEqual(["世界观", "总纲卷纲", "章纲"]);
    for (const s of PIPELINE_STEPS) expect(s.hint.length).toBeGreaterThan(0);
  });

  it("phase 显示名对齐三步", () => {
    expect(phaseDisplayName("worldview")).toBe("定世界观");
    expect(phaseDisplayName("outline")).toBe("定总纲卷纲");
    expect(phaseDisplayName("chapters")).toBe("定章纲");
  });
});
