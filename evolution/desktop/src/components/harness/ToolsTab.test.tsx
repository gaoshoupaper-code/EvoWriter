/**
 * ToolsTab 工具说明展示测试（REQ-20261125-210621 FR-003，AC-004/AC-005）。
 *
 * 覆盖：
 * - 每个挂载的工具显示说明全文，原有换行逐行显示
 * - 源码里读不到说明时，显示回退文字并标明「未读到源码里的说明」
 * - 没有任何说明文字时显示「用途资料不足。」，不出现标明
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { HarnessElementView, ToolInfo } from "@/lib/api";
import { ToolsTab } from "./ToolsTab";

const FULL = [
  "写一句「动作」：人物主动做了什么，旁人看得见。",
  "一句只写一个动作。",
  "一次只写一个完整的句子。",
].join("\n");

function agent(tools: string[]): HarnessElementView {
  return { name: "text", kind: "main", role: "main", prompt: { body: "" }, skills: [], middlewares: [], tools };
}

function tool(name: string, overrides: Partial<ToolInfo> = {}): ToolInfo {
  return {
    path: `tools/${name}.py`, name, description: null, description_source: "fallback",
    scope: { kind: "unknown" }, load_error: null, ...overrides,
  };
}

function textNodes(description: HTMLElement): (string | null)[] {
  // 换行处的 <br> 记作 "⏎"：说明全文按原有换行逐行显示，文字一字不差。
  return [...description.childNodes].map((node) => (node.nodeName === "BR" ? "⏎" : node.textContent));
}

describe("ToolsTab 工具说明", () => {
  it("显示说明全文，原有换行逐行显示", () => {
    render(<ToolsTab tools={[tool("write_action", { description: FULL, description_source: "source" })]}
                     agents={[agent(["write_action"])]} layout="manifest" />);

    const description = screen.getByText("write_action").closest("li")?.querySelector(".tool-desc") as HTMLElement;
    expect(textNodes(description)).toEqual([
      "写一句「动作」：人物主动做了什么，旁人看得见。", "⏎", "一句只写一个动作。", "⏎", "一次只写一个完整的句子。",
    ]);
    expect(screen.queryByText("未读到源码里的说明")).toBeNull();
  });

  it("源码里读不到说明时显示回退文字并标明", () => {
    render(<ToolsTab tools={[tool("write_action", { description: "逐句写作：写下一句动作。" })]}
                     agents={[agent(["write_action"])]} layout="manifest" />);

    const description = screen.getByText("write_action").closest("li")?.querySelector(".tool-desc") as HTMLElement;
    expect(textNodes(description)).toEqual(["逐句写作：写下一句动作。", "未读到源码里的说明"]);
  });

  it("没有任何说明文字时提示资料不足，不出现标明", () => {
    render(<ToolsTab tools={[tool("write_action")]} agents={[agent(["write_action"])]} layout="manifest" />);

    const description = screen.getByText("write_action").closest("li")?.querySelector(".tool-desc") as HTMLElement;
    expect(textNodes(description)).toEqual(["用途资料不足。"]);
  });
});
