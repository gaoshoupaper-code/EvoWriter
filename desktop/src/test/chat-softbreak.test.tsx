/**
 * REQ-20261007-200019 AC-001/AC-002：ChatPanel 换行渲染
 *
 * AC-001：assistant markdown 消息中单个 \n 渲染为真实换行（<br>），
 *         空行分段 / GFM 表格 / 列表 / 代码块等既有结构不受影响（对照组）。
 * AC-002：非 markdown 纯文本消息（含用户消息）带 pre-wrap 保留换行。
 *
 * 助手消息后跟一条用户消息：避免触发 ExecutionView（只对最后一条
 * assistant 渲染），让断言聚焦 markdown/纯文本两个渲染分支。
 */
import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { ChatPanel } from "../components/workspace/ChatPanel";
import type { ChatMessage } from "../lib/types";

const NOOP = vi.fn();

function renderPanel(messages: ChatMessage[]) {
  return render(
    <ChatPanel
      messages={messages}
      prompt=""
      loading={false}
      threads={[]}
      activeThreadId="t1"
      hasActiveWorkspace
      sessionMenuOpen={false}
      creatingThread={false}
      deleting={false}
      onPromptChange={NOOP}
      onSubmit={NOOP}
      onResumeSubmit={NOOP}
      onImageReviewSubmit={NOOP}
      onStop={NOOP}
      onToggleSessionMenu={NOOP}
      onCloseSessionMenu={NOOP}
      onCreateThread={NOOP}
      onSelectThread={NOOP}
      onDeleteThread={NOOP}
    />,
  );
}

describe("ChatPanel 换行渲染（REQ-20261007-200019）", () => {
  it("AC-001：markdown 消息的单个换行渲染为 <br>", () => {
    const { container } = renderPanel([
      { role: "assistant", content: "第一行\n第二行\n第三行", contentFormat: "markdown" },
      { role: "user", content: "收到" },
    ]);
    const markdown = container.querySelector(".message-content");
    // remark-breaks：段内 2 个软换行 → 2 个 <br>（修复前被折叠成空格，数量为 0）
    expect(markdown?.querySelectorAll("br").length).toBe(2);
    expect(markdown?.textContent).toContain("第一行");
    expect(markdown?.textContent).toContain("第三行");
  });

  it("AC-001 对照组：空行分段/表格/列表/代码块结构不变", () => {
    const content = [
      "段落一",
      "",
      "段落二",
      "",
      "| 列 | 值 |",
      "|---|---|",
      "| a | 1 |",
      "",
      "- 项目一",
      "- 项目二",
      "",
      "```",
      "code line",
      "```",
    ].join("\n");
    const { container } = renderPanel([
      { role: "assistant", content, contentFormat: "markdown" },
      { role: "user", content: "收到" },
    ]);
    const markdown = container.querySelector(".message-content");
    expect(markdown?.querySelectorAll("p").length).toBeGreaterThanOrEqual(2); // 空行分段
    expect(markdown?.querySelector("table")).toBeTruthy(); // GFM 表格
    expect(markdown?.querySelectorAll("li").length).toBe(2); // 列表
    expect(markdown?.querySelector("pre code")).toBeTruthy(); // 代码块
  });

  it("AC-002：纯文本消息带 pre-wrap 样式类保留换行", () => {
    const { container } = renderPanel([
      { role: "assistant", content: "第一行\n第二行", contentFormat: "markdown" },
      { role: "user", content: "问一\n问二", contentFormat: "text" },
    ]);
    const contents = container.querySelectorAll(".message-content");
    const plain = contents[contents.length - 1]?.querySelector("p");
    expect(plain?.textContent).toBe("问一\n问二"); // 换行符原样进 DOM（不折叠、不丢失）
    expect(plain).toHaveClass("message-plaintext"); // globals.css 中该类 = white-space: pre-wrap
  });

  it("AC-002 源头守卫：globals.css 中 .message-plaintext 规则存在且含 pre-wrap", () => {
    // jsdom 不加载样式表（vitest css:false），类名断言只能证明 DOM 侧；
    // 此处直读样式源头，防 CSS 规则被改名/删除后测试静默通过。
    const css = readFileSync("src/styles/globals.css", "utf-8");
    expect(css).toMatch(/\.message-content \.message-plaintext\s*\{[^}]*white-space:\s*pre-wrap/s);
  });
});
