/**
 * 布局契约测试（layout contract）——线上事故追补（REQ-20261012-202540 起源）：
 * jsdom 无布局、组件测试全绿也可能「渲染了但没有位置」。这里直接断言
 * 样式表关键结构。REQ-20261013-181350 追补4 契约为「照抄 ZCode 源码」：
 * dashboard 两栏、作品浮窗三态全部绝对定位悬浮（不占布局）、
 * 消息列两态同宽（容器查询公式驱动）、展开态列左移 168px、assistant 无气泡。
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const css = readFileSync(resolve(__dirname, "../styles/globals.css"), "utf-8");
/** 顶层规则（剥掉 @media 块，选择器定位不被断点内同名规则抢占；@container 保留参与断言） */
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

/** 提取 @container conversation 块正文（容器查询断言用） */
function containerBlock(condition: string): string {
  const re = new RegExp(
    `@container\\s+conversation\\s*\\(width\\s*>=\\s*${condition.replace(/[()]/g, "\\$&")}\\s*\\)\\s*\\{`,
  );
  const match = re.exec(css);
  expect(match, `样式表缺少容器查询块 ${condition}`).not.toBeNull();
  const start = css.indexOf("{", match!.index);
  let depth = 0;
  let end = -1;
  for (let i = start; i < css.length; i++) {
    if (css[i] === "{") depth++;
    if (css[i] === "}") {
      depth--;
      if (depth === 0) {
        end = i;
        break;
      }
    }
  }
  return css.slice(start, end + 1);
}

describe("dashboard 布局契约（作品浮窗悬浮 + 两态同宽）", () => {
  it("dashboard-body 两列（侧栏/主区）：浮窗三态均绝对定位，不再占第三列（DEC-015）", () => {
    const body = block(".dashboard-body");
    expect(body).toMatch(/grid-template-columns:\s*264px\s+minmax\(0,\s*1fr\)\s*;?/);
    expect(body).not.toMatch(/grid-template-columns:[^}]*auto/);
    expect(body).toContain("position: relative");
  });

  it("work-rail 展开态悬浮盖正文：absolute + 定宽 320 + 高度上限 + 浮卡外观（DEC-015）", () => {
    const rail = block(".work-rail");
    expect(rail).toContain("position: absolute");
    expect(rail).toContain("width: 320px");
    expect(rail).toContain("max-height: min(64dvh, 512px)");
    expect(rail).toContain("border-radius");
    expect(rail).toContain("box-shadow");
    expect(rail).not.toContain("margin: 12px");
    expect(rail).not.toContain("border-left");
  });

  it("mini 小卡与唤回按钮为浮窗，下移到标题带之下避开会话控件（DEC-005/006/013 追补）", () => {
    const mini = block(".work-rail.mini");
    expect(mini).toContain("position: absolute");
    expect(mini).toContain("top: 74px");
    expect(mini).toContain("width: 260px");
    const fab = block(".work-rail-fab");
    expect(fab).toContain("position: absolute");
    expect(fab).toContain("top: 74px");
    expect(fab).toContain("z-index");
  });

  it("对话容器为容器查询上下文（DEC-016：断点量对话区自身，不量窗口）", () => {
    const main = block(".dashboard-main");
    expect(main).toMatch(/container:\s*conversation\s*\/\s*inline-size/);
    // 旧窗口断点媒体查询彻底退场
    expect(css).not.toMatch(/@media\s*\(\s*min-width:\s*1896px\s*\)/);
  });

  it("消息列两态同宽（DEC-014）：容器查询公式，不带 :has 状态条件", () => {
    const mid = containerBlock("864px");
    expect(mid).toContain(".message-list");
    expect(mid).toContain(".chat-composer");
    expect(mid).toContain("max-width: min(calc(100% - 6rem), 56rem)");
    expect(mid).not.toContain(":has(");

    const wide = containerBlock("1280px");
    expect(wide).toContain(".message-list");
    expect(wide).toContain(".chat-composer");
    expect(wide).toContain("max-width: min(calc(100% - 24rem), 72rem)");
    // 两态同宽：宽度规则不得挂在 :has(展开态) 下，只有位移可以
    const widthInWide = wide.slice(0, wide.indexOf("transform") > -1 ? wide.indexOf("transform") : undefined);
    expect(widthInWide).not.toContain(":has(");
  });

  it("展开态列左移 168px 仅挂 :has + 容器 ≥1280px（DEC-015，ZCode -translate-x-42）", () => {
    const wide = containerBlock("1280px");
    expect(wide).toContain(":has(> .work-rail:not(.mini)) .message-list");
    expect(wide).toContain(":has(> .work-rail:not(.mini)) .chat-composer");
    expect(wide).toContain("transform: translateX(-168px)");
  });

  it("切换过渡遵守动效变量，≥1280 只过渡 transform", () => {
    const list = block(".message-list");
    expect(list).toMatch(/transition:[^;]*width[^;]*max-width[^;]*transform[^;]*var\(--motion-duration\)/);
    const wide = containerBlock("1280px");
    expect(wide).toMatch(/transition-property:\s*transform/);
  });
});

describe("对话区对齐 ZCode 契约（FR-002/FR-003）", () => {
  it("消息列铺满 + 居中，760px 定宽退场（DEC-014）", () => {
    const list = block(".message-list");
    // width:100% 必须显式：grid 项 + auto 边距不 stretch，缺了会按内容收缩（线上测量踩过）
    expect(list).toContain("width: 100%");
    expect(list).toContain("margin-inline: auto");
    expect(list).not.toContain("760px");
    expect(css).not.toContain("max-width: 760px");
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
    expect(composer).toContain("width: 100%");
    expect(composer).toContain("margin-inline: auto");
    expect(composer).not.toContain("760px");
  });
});

describe("视觉升级（REQ-20261018-202005）", () => {
  it("顶栏固定48px，主体使用剩余高度，列表独立滚动", () => {
    expect(block(".dashboard-shell")).toContain("grid-template-rows: 48px minmax(0, 1fr)");
    expect(block(".dashboard-shell")).toContain("height: 100dvh");
    expect(block(".dashboard-sidebar")).toContain("flex-direction: column");
    expect(block(".sidebar-nav")).toContain("overflow-y: auto");
    expect(block(".sidebar-footer")).toContain("flex-shrink: 0");
    expect(block(".conversation-panel")).not.toContain("77px");
  });

  it("侧栏三标题为小号淡色标签，条目默认13px（REQ-20261020-200710 DEC-005）", () => {
    expect(block(".sidebar-group-label")).toContain("font-size: var(--text-ui-xs)");
    expect(block(".sidebar-group-label")).toContain("font-weight: 800");
    expect(block(".sidebar-group-label")).toContain("letter-spacing: 0.06em");
    expect(block(".sidebar-book-title")).toContain("font-size: var(--text-ui-base)");
    expect(block(".sidebar-tree-item")).toContain("font-size: var(--text-ui-base)");
  });

  it("输入气泡包裹操作，文本框无独立边框", () => {
    expect(block(".chat-input-wrap")).toContain("border-radius: var(--radius-xl)");
    expect(block(".chat-input")).toContain("border: 0");
    expect(block(".chat-composer")).not.toContain("104px");
    expect(block(".composer-actions")).toContain("justify-content: flex-end");
  });
});

describe("工作台铺满窗口 + 字号基准（对齐 ZCode）", () => {
  it("dashboard-shell 全幅：无 1680px 居中封顶（超宽窗口两侧留白）", () => {
    const shell = block(".dashboard-shell");
    expect(shell).toContain("width: 100%");
    expect(shell).not.toContain("1680");
    expect(shell).not.toContain("margin: 0 auto");
  });

  it("字号唯一基准为字面量 13px（var 写法会与 --text-ui-base 成环失效回落 16px）", () => {
    expect(css).toMatch(/--ui-font-size:\s*13px/);
    expect(css).not.toMatch(/--ui-font-size:\s*var\(/);
    // 未显式设 token 的文字（聊天正文/Markdown 段落）经 body 兜底继承基准，不回落 16px
    expect(css).toMatch(/body\s*\{[^}]*font-size:\s*var\(--text-ui-base\)/s);
  });

  // REQ-20261021-100317 FR-001/DEC-001/DEC-005：正文全路径字号统一为基准 +1px。
  // 拍板后续跑走行消息路径（yan-text-row），此前用 sm 与 message-content 的 lg
  // 相差 4px——拍板前后正文忽大忽小。统一档位 --text-ui-md，跟随设置联动。
  it("正文全路径统一基准 +1px（--text-ui-md）：消息正文/大纲/行消息正文/提问文案", () => {
    expect(css).toMatch(/--text-ui-md:\s*calc\(var\(--ui-font-size\)\s*\+\s*1px\)/);
    // 成对跨行选择器（block() 锚定不了换行），直接对文本断言
    expect(css).toMatch(/\.message-content,\s*\.outline-markdown\s*\{[^}]*font-size:\s*var\(--text-ui-md\)/);
    expect(block(".yan-text-row")).toContain("font-size: var(--text-ui-md)");
    expect(block(".interview-question")).toContain("font-size: var(--text-ui-md)");
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
