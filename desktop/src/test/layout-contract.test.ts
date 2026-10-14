/**
 * 布局契约测试（layout contract）——线上事故追补（REQ-20261012-202540 起源）：
 * jsdom 无布局、组件测试全绿也可能「渲染了但没有位置」。这里直接断言
 * 样式表关键结构。REQ-20261013-181350 起契约更新为浮窗形态：
 * dashboard 两栏、作品浮窗绝对定位、消息定位居中、assistant 无气泡。
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

describe("dashboard 布局契约（作品面板 dock + 浮窗双形态）", () => {
  it("dashboard-body 三列（侧栏/主区/作品面板），relative 供 mini 浮窗定位", () => {
    const body = block(".dashboard-body");
    expect(body).toContain("grid-template-columns");
    expect(body).toMatch(/grid-template-columns:\s*252px\s+minmax\(0,\s*1fr\)\s+auto/);
    expect(body).toContain("position: relative");
  });

  it("work-rail 展开态浮窗化停靠：占第三列让对话让位，但呈悬浮卡片（非贴边侧栏）", () => {
    const rail = block(".work-rail");
    expect(rail).toContain("width: 300px");
    expect(rail).toContain("margin: 12px");
    expect(rail).toContain("border-radius");
    expect(rail).toContain("box-shadow");
    expect(rail).not.toContain("position: absolute");
    expect(rail).not.toContain("border-left");
  });

  it("mini 小卡与唤回按钮为浮窗，下移到标题带之下避开会话控件（DEC-005/006 追补）", () => {
    const mini = block(".work-rail.mini");
    expect(mini).toContain("position: absolute");
    expect(mini).toContain("top: 74px");
    expect(mini).toContain("width: 260px");
    const fab = block(".work-rail-fab");
    expect(fab).toContain("position: absolute");
    expect(fab).toContain("top: 74px");
    expect(fab).toContain("z-index");
  });

  it("dock 展开时消息列与输入框解除 760 上限（对话让位占满），收起回居中定宽", () => {
    const anchor = ":has(> .work-rail:not(.mini)) .message-list";
    const idx = cssTopLevel.indexOf(anchor);
    expect(idx).toBeGreaterThan(-1);
    const ruleText = cssTopLevel.slice(idx, cssTopLevel.indexOf("}", idx));
    expect(ruleText).toContain(".chat-composer");
    expect(ruleText).toContain("max-width: none");
  });
});

describe("对话区对齐 ZCode 契约（FR-002/FR-003）", () => {
  it("消息列定宽 760px 居中（DEC-004）", () => {
    const list = block(".message-list");
    // width:100% 必须显式：grid 项 + auto 边距不 stretch，缺了会按内容收缩（线上测量踩过）
    expect(list).toContain("width: 100%");
    expect(list).toContain("max-width: 760px");
    expect(list).toContain("margin-inline: auto");
  });

  it("assistant 消息无气泡（DEC-001/FR-002），用户消息气泡保留（DEC-003）", () => {
    const assistant = block(".message.assistant");
    expect(assistant).toContain("border: 0");
    expect(assistant).toContain("background: none");
    const user = block(".message.user");
    expect(user).toContain("border-radius");
    expect(user).toContain("background");
  });

  it("composer 与消息列同宽居中（DEC-007）", () => {
    const composer = block(".chat-composer");
    expect(composer).toContain("max-width: 760px");
    expect(composer).toContain("margin-inline: auto");
  });
});

describe("极窄断点（≤920px）", () => {
  it("隐藏作品浮窗与唤回按钮的同时网格退回单列", () => {
    const narrow = css.match(/@media\s*\(max-width:\s*920px\)\s*{([\s\S]*?)\n}/);
    expect(narrow).not.toBeNull();
    expect(narrow![1]).toContain("grid-template-columns: 1fr");
    expect(narrow![1]).toContain(".work-rail");
    expect(narrow![1]).toContain(".work-rail-fab");
  });
});
