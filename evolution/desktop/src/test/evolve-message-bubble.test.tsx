/**
 * REQ-20261007-200019 AC-001/AC-002：EvolveMessageBubble 换行渲染
 *
 * 首份组件测试（DEC-004）。
 * AC-001：assistant 消息 markdown 中单个 \n 渲染为真实换行（<br>），
 *         空行分段 / GFM 表格结构不变（对照组）。
 * AC-002：用户消息气泡 .msg-text 携带 pre-wrap 类（globals.css 已有
 *         white-space: pre-wrap 规则——该部分现状已满足，此处固化类名防回归）。
 */
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import EvolveMessageBubble from "@/components/evolve/EvolveMessageBubble";
import type { EvolveMessage } from "@/lib/api";

function msg(partial: Partial<EvolveMessage>): EvolveMessage {
  return {
    id: "m1",
    session_id: "s1",
    role: "assistant",
    content: "",
    seq: 1,
    created_at: "2026-10-07T00:00:00Z",
    ...partial,
  };
}

describe("EvolveMessageBubble 换行渲染（REQ-20261007-200019）", () => {
  it("AC-001：assistant 消息的单个换行渲染为 <br>", () => {
    const { container } = render(
      <EvolveMessageBubble
        message={msg({ role: "assistant", content: "第一行\n第二行\n第三行" })}
        points={[]}
      />,
    );
    const markdown = container.querySelector(".msg-markdown");
    expect(markdown?.querySelectorAll("br").length).toBe(2); // 修复前折叠为空格，数量为 0
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
    const { container } = render(
      <EvolveMessageBubble message={msg({ role: "assistant", content })} points={[]} />,
    );
    const markdown = container.querySelector(".msg-markdown");
    expect(markdown?.querySelectorAll("p").length).toBeGreaterThanOrEqual(2);
    expect(markdown?.querySelector("table")).toBeTruthy();
    expect(markdown?.querySelectorAll("li").length).toBe(2);
    expect(markdown?.querySelector("pre code")).toBeTruthy();
  });

  it("AC-002：用户消息气泡携带 .msg-text（pre-wrap 载体类）", () => {
    const { container } = render(
      <EvolveMessageBubble message={msg({ role: "user", content: "问一\n问二" })} points={[]} />,
    );
    const text = container.querySelector(".msg-bubble .msg-text");
    expect(text?.textContent).toBe("问一\n问二"); // 换行符原样进 DOM
  });
});
