/**
 * 五段制阶段共享模块测试（REQ-20261018-115508 FR-011）。
 * 覆盖：五步完成度推导（目录即进度）、活跃步匹配、相位文案。
 * REQ-20261018-175526 FR-013：显示术语统一「主题」，匹配仍对齐后端契约「题材」。
 */
import { describe, expect, it } from "vitest";
import {
  PIPELINE_STEPS,
  activeStepIndex,
  displayUnitLabel,
  isStepDone,
  phaseDisplayName,
  stageGuide,
  stageLabel,
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

  it("后端 next_unit 用契约标签「题材」时仍匹配主题步（FR-013 显示/匹配分离）", () => {
    expect(activeStepIndex({ kind: "five", phase: "theme", next_unit: "题材", awaiting: {} }, [])).toBe(0);
  });

  it("该步已完成 → 不高亮（-1）", () => {
    expect(activeStepIndex({ kind: "five", phase: "text", next_unit: "正文", awaiting: {} }, FULL_FILES)).toBe(-1);
  });
});

describe("phaseDisplayName 相位文案", () => {
  it("五段各有文案，未知值原样返回", () => {
    expect(phaseDisplayName("theme")).toBe("定主题");
    expect(phaseDisplayName("outline")).toBe("定总纲");
    expect(phaseDisplayName("volume")).toBe("定卷纲");
    expect(phaseDisplayName("chapter")).toBe("定章纲");
    expect(phaseDisplayName("text")).toBe("写正文");
    expect(phaseDisplayName("mystery")).toBe("mystery");
  });
});

describe("FR-013 术语统一「主题」", () => {
  it("PIPELINE_STEPS 显示「主题」，match 保留契约标签「题材」", () => {
    expect(PIPELINE_STEPS[0].label).toBe("主题");
    expect(PIPELINE_STEPS[0].match).toBe("题材");
    for (const s of PIPELINE_STEPS.slice(1)) {
      expect(s.match).toBe(s.label);
    }
  });

  it("stageLabel 五段映射，未知/legacy 返回 null", () => {
    expect(stageLabel("theme")).toBe("主题");
    expect(stageLabel("outline")).toBe("总纲");
    expect(stageLabel("volume")).toBe("卷纲");
    expect(stageLabel("chapter")).toBe("章纲");
    expect(stageLabel("text")).toBe("正文");
    expect(stageLabel("legacy")).toBeNull();
    expect(stageLabel("mystery")).toBeNull();
  });

  it("displayUnitLabel 把后端单元标签「题材」显示为「主题」，其余原样", () => {
    expect(displayUnitLabel("题材")).toBe("主题");
    expect(displayUnitLabel("总纲")).toBe("总纲");
    expect(displayUnitLabel("旧版工作区（只读）")).toBe("旧版工作区（只读）");
  });
});

describe("FR-003 阶段引导语", () => {
  it("五段各有引导语与示例问题", () => {
    for (const phase of ["theme", "outline", "volume", "chapter", "text"] as const) {
      const guide = stageGuide(phase);
      expect(guide).not.toBeNull();
      expect(guide!.text.length).toBeGreaterThan(0);
      expect(guide!.suggestions.length).toBeGreaterThanOrEqual(1);
      expect(guide!.suggestions.length).toBeLessThanOrEqual(2);
    }
  });

  it("未知/legacy 阶段返回 null（调用方回退通用引导）", () => {
    expect(stageGuide("legacy")).toBeNull();
    expect(stageGuide("mystery")).toBeNull();
  });
});
