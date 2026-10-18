/**
 * 五段制阶段共享模块测试（REQ-20261018-115508 FR-011）。
 * 覆盖：五步完成度推导（目录即进度）、活跃步匹配、相位文案。
 */
import { describe, expect, it } from "vitest";
import {
  PIPELINE_STEPS,
  activeStepIndex,
  isStepDone,
  phaseDisplayName,
} from "@/lib/pipeline-steps";

const FULL_FILES = [
  "主题.md",
  "总纲.md",
  "卷纲/第01卷.md",
  "章纲/第001章.md",
  "正文/第001章开局.md",
];

describe("isStepDone 完成度推导", () => {
  it("第 0 步看主题.md", () => {
    expect(isStepDone(0, ["主题.md"])).toBe(true);
    expect(isStepDone(0, ["总纲.md"])).toBe(false);
  });

  it("第 1 步看总纲.md", () => {
    expect(isStepDone(1, ["主题.md"])).toBe(false);
    expect(isStepDone(1, ["总纲.md"])).toBe(true);
  });

  it("第 2 步看至少一份卷纲", () => {
    expect(isStepDone(2, ["总纲.md"])).toBe(false);
    expect(isStepDone(2, ["卷纲/第01卷.md"])).toBe(true);
    expect(isStepDone(2, ["卷纲/第12卷.md"])).toBe(true);
  });

  it("第 3 步看至少一份章纲", () => {
    expect(isStepDone(3, ["章纲/第001章.md"])).toBe(true);
    expect(isStepDone(3, ["卷纲/第01卷.md"])).toBe(false);
  });

  it("第 4 步看至少一份正文（允许带章名）", () => {
    expect(isStepDone(4, ["正文/第001章一百级回家.md"])).toBe(true);
    expect(isStepDone(4, ["正文/第012章.md"])).toBe(true);
    expect(isStepDone(4, ["章纲/第001章.md"])).toBe(false);
  });

  it("五步全绿", () => {
    for (let n = 0; n < 5; n++) {
      expect(isStepDone(n, FULL_FILES)).toBe(true);
    }
    expect(PIPELINE_STEPS).toHaveLength(5);
  });
});

describe("activeStepIndex 活跃步", () => {
  it("next_unit 含未完成步名时高亮该步", () => {
    const rels = ["主题.md", "总纲.md"];
    expect(activeStepIndex({ kind: "five", phase: "volume", next_unit: "卷纲", awaiting: {} }, rels)).toBe(2);
    expect(activeStepIndex({ kind: "five", phase: "chapter", next_unit: "章纲", awaiting: {} }, rels)).toBe(3);
  });

  it("该步已完成 → 不高亮（-1）", () => {
    expect(activeStepIndex({ kind: "five", phase: "text", next_unit: "正文", awaiting: {} }, FULL_FILES)).toBe(-1);
  });
});

describe("phaseDisplayName 相位文案", () => {
  it("五段各有文案，未知值原样返回", () => {
    expect(phaseDisplayName("theme")).toBe("定题材");
    expect(phaseDisplayName("outline")).toBe("定总纲");
    expect(phaseDisplayName("volume")).toBe("定卷纲");
    expect(phaseDisplayName("chapter")).toBe("定章纲");
    expect(phaseDisplayName("text")).toBe("写正文");
    expect(phaseDisplayName("mystery")).toBe("mystery");
  });
});
