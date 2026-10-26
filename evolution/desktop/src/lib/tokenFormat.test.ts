/**
 * token 万分位格式化单测（REQ-20261027-012631 FR-005 / DEC-012 / AC-008）。
 * 统一口径：一律以万为单位保留 1 位小数；无数据显示「—」不显示 0。
 */
import { describe, expect, it } from "vitest";
import { formatTokensW } from "./tokenFormat";

describe("formatTokensW", () => {
  it("大数换算为 w 并保留 1 位小数", () => {
    expect(formatTokensW(12345678)).toBe("1234.6w");
  });

  it("不足 1 万如实显示", () => {
    expect(formatTokensW(8321)).toBe("0.8w");
    expect(formatTokensW(499)).toBe("0.0w");
  });

  it("0 显示 0w（有数据但为 0 ≠ 无数据）", () => {
    expect(formatTokensW(0)).toBe("0w");
  });

  it("无数据显示占位符，不得显示 0", () => {
    expect(formatTokensW(null)).toBe("—");
    expect(formatTokensW(undefined)).toBe("—");
  });
});
