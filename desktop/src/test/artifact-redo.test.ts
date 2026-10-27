/**
 * 删除重做客户端资格判定测试（REQ-20261027-213246 修订 2026-10-28）。
 * 与服务端 artifact_redo.redo_eligibility 同口径：总纲需下游全空；
 * 卷纲需最大卷号且章纲/正文全空。
 */
import { describe, expect, it } from "vitest";
import { redoEligibility, redoTargetKind } from "@/lib/artifact-redo";

const f = (...rels: string[]) => rels.map((rel) => ({ rel }));

describe("redoEligibility", () => {
  it("范围：只有总纲与卷纲可删重做", () => {
    expect(redoTargetKind("总纲.md")).toBe("outline");
    expect(redoTargetKind("卷纲/第02卷.md")).toBe("volume");
    expect(redoTargetKind("文风.md")).toBeNull();
    expect(redoTargetKind("章纲/第001章.md")).toBeNull();
    expect(redoEligibility("文风.md", []).eligible).toBe(false);
  });

  it("总纲：无任何卷纲/章纲/正文才可删", () => {
    expect(redoEligibility("总纲.md", f("主题.md", "总纲.md"))).toEqual({ eligible: true, reason: "" });
    expect(redoEligibility("总纲.md", f("总纲.md", "卷纲/第01卷.md")).eligible).toBe(false);
    expect(redoEligibility("总纲.md", f("总纲.md", "章纲/第001章.md")).eligible).toBe(false);
    expect(redoEligibility("总纲.md", f("总纲.md", "正文/第001章开局.md")).eligible).toBe(false);
  });

  it("卷纲：仅最大卷号且无章纲/正文可删", () => {
    const vols = f("总纲.md", "卷纲/第01卷.md", "卷纲/第02卷.md");
    expect(redoEligibility("卷纲/第01卷.md", vols).eligible).toBe(false);
    expect(redoEligibility("卷纲/第02卷.md", vols)).toEqual({ eligible: true, reason: "" });
    const withChapter = [...vols, { rel: "章纲/第001章.md" }];
    expect(redoEligibility("卷纲/第02卷.md", withChapter).eligible).toBe(false);
    expect(redoEligibility("卷纲/第02卷.md", [...vols, { rel: "正文/第001章开局.md" }]).eligible).toBe(false);
  });

  it("不可删时给出人话原因（按钮 tooltip 用）", () => {
    const r = redoEligibility("总纲.md", f("总纲.md", "卷纲/第01卷.md"));
    expect(r.reason).toContain("卷纲");
    expect(r.reason).toContain("最下游");
  });
});
