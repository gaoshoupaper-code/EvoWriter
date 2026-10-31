/**
 * 删除重做客户端资格判定测试（REQ-20261030-211446 全量开放：六类产物）。
 * 与服务端 artifact_redo.redo_eligibility 同口径（从下往上不级联 +
 * 任意位置可删）；另锁 findArtifactGaps 的内部洞语义（删哪补哪）。
 */
import { describe, expect, it } from "vitest";
import { findArtifactGaps, redoEligibility, redoTargetKind } from "@/lib/artifact-redo";

const f = (...entries: Array<[rel: string, markdown?: string]>) =>
  entries.map(([rel, markdown = ""]) => ({ rel, markdown }));

const vol = (n: number, range?: [number, number]) =>
  [`卷纲/第0${n}卷.md`, `# 卷纲 · 第 ${n} 卷\n> 状态：定稿\n${range ? `\n本卷章号范围：第${range[0]}-${range[1]}章\n` : ""}`] as [string, string];

describe("redoTargetKind", () => {
  it("六类产物均可删重做，范围外为 null", () => {
    expect(redoTargetKind("主题.md")).toBe("theme");
    expect(redoTargetKind("总纲.md")).toBe("outline");
    expect(redoTargetKind("文风.md")).toBe("style");
    expect(redoTargetKind("卷纲/第02卷.md")).toBe("volume");
    expect(redoTargetKind("章纲/第005章.md")).toBe("chapter");
    expect(redoTargetKind("正文/第005章重写这章.md")).toBe("text");
    expect(redoTargetKind("设定/角色.md")).toBeNull();
  });
});

describe("redoEligibility", () => {
  it("主题：总纲存在即拒", () => {
    expect(redoEligibility("主题.md", f(["主题.md"], ["总纲.md"])).eligible).toBe(false);
    expect(redoEligibility("主题.md", f(["主题.md"]))).toEqual({ eligible: true, reason: "" });
  });

  it("总纲：无任何卷纲/章纲/正文才可删（文风不算下游）", () => {
    const base = f(["总纲.md"], ["文风.md"]);
    expect(redoEligibility("总纲.md", base)).toEqual({ eligible: true, reason: "" });
    expect(redoEligibility("总纲.md", f(["总纲.md"], ["文风.md"], vol(1))).eligible).toBe(false);
    expect(redoEligibility("总纲.md", f(["总纲.md"], ["章纲/第001章.md"])).eligible).toBe(false);
    expect(redoEligibility("总纲.md", f(["总纲.md"], ["正文/第001章开局.md"])).eligible).toBe(false);
  });

  it("卷纲：按本卷章号范围判定，中间卷可删", () => {
    const files = f(["总纲.md"], vol(1, [1, 10]), vol(2, [11, 20]), ["章纲/第015章.md"]);
    // 章纲 15 落第 2 卷范围：第 2 卷拒、第 1 卷放行（即使第 1 卷不是最大卷号）
    expect(redoEligibility("卷纲/第02卷.md", files).eligible).toBe(false);
    expect(redoEligibility("卷纲/第01卷.md", files)).toEqual({ eligible: true, reason: "" });
  });

  it("卷纲：范围解析失败兜底——章纲非空即拒", () => {
    const noRange = f(["总纲.md"], vol(1), ["章纲/第099章.md"]);
    expect(redoEligibility("卷纲/第01卷.md", noRange).eligible).toBe(false);
    expect(redoEligibility("卷纲/第01卷.md", f(["总纲.md"], vol(1)))).toEqual({ eligible: true, reason: "" });
  });

  it("章纲：该章正文存在即拒", () => {
    expect(redoEligibility("章纲/第005章.md", f(["章纲/第005章.md"], ["正文/第005章.md"])).eligible).toBe(false);
    expect(redoEligibility("章纲/第005章.md", f(["章纲/第005章.md"], ["正文/第005章重写.md"])).eligible).toBe(false);
    expect(redoEligibility("章纲/第005章.md", f(["章纲/第005章.md"], ["正文/第006章.md"]))).toEqual({ eligible: true, reason: "" });
  });

  it("正文与文风：全产物在场也可删", () => {
    const full = f(
      ["主题.md"], ["总纲.md"], ["文风.md"], vol(1, [1, 3]),
      ["章纲/第001章.md"], ["章纲/第002章.md"], ["章纲/第003章.md"],
      ["正文/第001章.md"], ["正文/第002章.md"], ["正文/第003章.md"],
    );
    expect(redoEligibility("正文/第003章.md", full)).toEqual({ eligible: true, reason: "" });
    expect(redoEligibility("文风.md", full)).toEqual({ eligible: true, reason: "" });
  });

  it("人物卡：叶级产物全产物在场也可删（REQ-20261031-183257）", () => {
    const full = f(
      ["主题.md"],
      ["总纲.md"],
      ["文风.md"],
      ["人物/主角.md"],
      ["人物/BOSS·卷一·恶霸工头.md"],
      ["人物/老陈.md"],
      ...[vol(1)],
      ["章纲/第001章.md"],
      ["正文/第001章.md"]
    );
    expect(redoTargetKind("人物/主角.md")).toBe("character");
    expect(redoTargetKind("人物/BOSS·卷一·恶霸工头.md")).toBe("character");
    expect(redoEligibility("人物/主角.md", full)).toEqual({ eligible: true, reason: "" });
    expect(redoEligibility("人物/老陈.md", full)).toEqual({ eligible: true, reason: "" });
  });

  it("不可删时给出人话原因（按钮 tooltip 用）", () => {
    const r = redoEligibility("总纲.md", f(["总纲.md"], ...[vol(1)]));
    expect(r.reason).toContain("卷纲");
    expect(r.reason).toContain("最下游");
  });
});

describe("findArtifactGaps", () => {
  const ch = (ns: number[]) => ns.map((n) => [`章纲/第${String(n).padStart(3, "0")}章.md`] as [string]);
  const tx = (ns: number[]) => ns.map((n) => [`正文/第${String(n).padStart(3, "0")}章.md`] as [string]);

  it("删中间章留下的内部洞被识别", () => {
    const gaps = findArtifactGaps([...ch([1, 2, 3, 5, 6]), ...tx([1, 2, 4])].map(([rel]) => ({ rel })));
    expect(gaps.outline).toEqual([4]);
    expect(gaps.text).toEqual([3]);
  });

  it("顺序创作（尾部未写）不算缺失；单章/空集无洞", () => {
    const gaps = findArtifactGaps([...ch([1, 2, 3]), ...tx([1])].map(([rel]) => ({ rel })));
    expect(gaps.outline).toEqual([]);
    expect(gaps.text).toEqual([]);
    expect(findArtifactGaps([{ rel: "正文/第001章.md" }]).text).toEqual([]);
    expect(findArtifactGaps([])).toEqual({ outline: [], text: [] });
  });
});
