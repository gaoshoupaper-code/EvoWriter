import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";

const css = readFileSync("src/styles/globals.css", "utf8");
const light = css.match(/:root\s*\{([\s\S]*?)\n\}/)![1];
const dark = css.match(/html\[data-theme="dark"\]\s*\{([\s\S]*?)\n\}/)![1];
function value(source: string, name: string) {
  return source.match(new RegExp(`${name}:\\s*(#[a-fA-F0-9]+)`))![1];
}
function luminance(hex: string) {
  const rgb = hex.slice(1).match(/../g)!.map((c) => parseInt(c, 16) / 255)
    .map((v) => v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
  return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
}
function contrast(a: string, b: string) {
  const x = luminance(a), y = luminance(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

describe("主题可读性（AC-005）", () => {
  for (const [theme, source] of [["light", light], ["dark", dark]]) {
    for (const fg of ["--ink", "--muted"]) {
      for (const bg of ["--bg", "--chrome", "--panel-solid"]) {
        it(`${theme} ${fg} on ${bg} >= 4.5`, () => {
          expect(contrast(value(source, fg), value(source, bg))).toBeGreaterThanOrEqual(4.5);
        });
      }
    }
  }
  it("用户气泡角色标签不沿用旧白字", () => {
    expect(css).toMatch(/\.message\.user \.message-role\s*\{\s*color: var\(--ink\)/);
  });
  it("浅色正文较原面板低亮，侧栏比正文更深", () => {
    expect(luminance(value(light, "--bg"))).toBeLessThan(luminance("#f5ecd4"));
    expect(luminance(value(light, "--chrome"))).toBeLessThan(luminance(value(light, "--bg")));
  });
});
