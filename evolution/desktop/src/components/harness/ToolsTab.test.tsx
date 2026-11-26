/**
 * ToolsTab 工具说明展示测试。
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
  "写一段「信息」：定时间、地点、空间布局与相对距离。",
  "一段只锚定一组空间关系。",
  "一次只写一个完整的段落。",
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
    render(<ToolsTab tools={[tool("write_info", { description: FULL, description_source: "source" })]}
                     agents={[agent(["write_info"])]} layout="manifest" />);

    const description = screen.getByText("write_info").closest("li")?.querySelector(".tool-desc") as HTMLElement;
    expect(textNodes(description)).toEqual([
      "写一段「信息」：定时间、地点、空间布局与相对距离。", "⏎", "一段只锚定一组空间关系。", "⏎", "一次只写一个完整的段落。",
    ]);
    expect(screen.queryByText("未读到源码里的说明")).toBeNull();
  });

  it("源码里读不到说明时显示回退文字并标明", () => {
    render(<ToolsTab tools={[tool("write_info", { description: "逐段写作：写信息段，定时间地点、空间布局与相对距离。" })]}
                     agents={[agent(["write_info"])]} layout="manifest" />);

    const description = screen.getByText("write_info").closest("li")?.querySelector(".tool-desc") as HTMLElement;
    expect(textNodes(description)).toEqual(["逐段写作：写信息段，定时间地点、空间布局与相对距离。", "未读到源码里的说明"]);
  });

  it("没有任何说明文字时提示资料不足，不出现标明", () => {
    render(<ToolsTab tools={[tool("write_info")]} agents={[agent(["write_info"])]} layout="manifest" />);

    const description = screen.getByText("write_info").closest("li")?.querySelector(".tool-desc") as HTMLElement;
    expect(textNodes(description)).toEqual(["用途资料不足。"]);
  });
});
