/**
 * 布局契约测试（layout contract）——线上事故追补：
 * 作品栏作为 dashboard-body 的第三个 grid 子元素渲染，但样式表没给它
 * 第三列，被挤进屏幕外隐式行，用户完全看不到；jsdom 无布局、测试全绿。
 * 这里直接断言样式表关键结构，防「组件渲染了但没有位置」类回归。
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const css = readFileSync(resolve(__dirname, "../styles/globals.css"), "utf-8");
/** 顶层规则（剥掉 @media 块，选择器定位不被断点内同名规则抢占） */
const cssTopLevel = css.replace(/@media[^{]+\{[\s\S]*?\n\}/g, "");

/** 提取选择器块主体（锚定「选择器 + 空格 + {」，避免 .work-rail 误配 .work-rail-body） */
function block(selector: string): string {
  const anchor = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\s*\\{";
  const match = new RegExp(anchor).exec(cssTopLevel);
  expect(match, `样式表缺少选择器 ${selector}`).not.toBeNull();
  const start = cssTopLevel.indexOf("{", match!.index);
  let depth = 0;
  let end = -1;
  for (let i = start; i < cssTopLevel.length; i++) {
    if (cssTopLevel[i] === "{") depth++;
    if (cssTopLevel[i] === "}") {
      depth--;
      if (depth === 0) {
        end = i;
        break;
      }
    }
  }
  return cssTopLevel.slice(start, end + 1);
}

describe("dashboard 布局契约（作品栏必须有位置）", () => {
  it("dashboard-body 为侧栏/主区/作品栏保留三列，第三列 auto 由 rail 宽度决定", () => {
    const body = block(".dashboard-body");
    expect(body).toContain("grid-template-columns");
    expect(body).toMatch(/grid-template-columns:\s*252px\s+minmax\(0,\s*1fr\)\s+auto/);
  });

  it("work-rail 有明确宽度与左边界（展开/收起两态）", () => {
    const rail = block(".work-rail");
    expect(rail).toContain("width: 300px");
    expect(rail).toContain("border-left");
    const collapsed = block(".work-rail.collapsed");
    expect(collapsed).toContain("width: 44px");
  });

  it("极窄断点（≤920px）隐藏作品栏的同时网格退回单列", () => {
    const narrow = css.match(/@media\s*\(max-width:\s*920px\)\s*{([\s\S]*?)\n}/);
    expect(narrow).not.toBeNull();
    expect(narrow![1]).toContain("grid-template-columns: 1fr");
    expect(narrow![1]).toContain(".work-rail");
  });
});
