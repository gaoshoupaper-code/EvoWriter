import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { HarnessElementView, ToolInfo } from "@/lib/api";
import { MiddlewareTab } from "./MiddlewareTab";
import { ToolsTab } from "./ToolsTab";

const tool = (name: string, description: string): ToolInfo => ({
  path: `tools/${name}.py`, name, description, scope: { kind: "unknown" }, load_error: null,
});

const agent = (name: string, tools: string[]): HarnessElementView => ({
  name, kind: "main", prompt: { body: "" }, skills: [], middlewares: [], tools,
});

afterEach(cleanup);

describe("Harness 资产用途", () => {
  it("按角色显示工具名称和用途，区分空名单与历史数据不足", () => {
    const tools = [
      tool("read_artifact", "读取指定的章纲或正文。"),
      tool("save_artifact", "保存或修改创作产物。"),
    ];
    const { rerender } = render(<ToolsTab tools={tools} agents={[agent("text", ["save_artifact"])]} layout="manifest" />);

    expect(screen.getByText("save_artifact")).toBeTruthy();
    expect(screen.getByText("保存或修改创作产物。")).toBeTruthy();
    expect(screen.queryByText("read_artifact")).toBeNull();
    expect(screen.queryByText(/未登记作用域/)).toBeNull();

    rerender(<ToolsTab tools={tools} agents={[agent("review", ["read_artifact"])]} layout="manifest" />);
    expect(screen.getByText("读取指定的章纲或正文。")).toBeTruthy();
    expect(screen.queryByText("save_artifact")).toBeNull();

    rerender(<ToolsTab tools={tools} agents={[agent("review", [])]} layout="manifest" />);
    expect(screen.getByText("当前角色未配置工具。")).toBeTruthy();

    rerender(<ToolsTab tools={tools} agents={[agent("review", [])]} layout="v7" />);
    expect(screen.getByText(/数据不足/)).toBeTruthy();
    expect(screen.queryByText("当前角色未配置工具。")).toBeNull();
  });

  it("按六个钩子展示各自的用途和详情，保留空钩子的标题", () => {
    const main = agent("text", []);
    main.middlewares = [{
      hook: "before_agent", hooks: ["before_agent", "wrap_tool_call"],
      class_name: "RevisionLimitMiddleware", group: "agent", params: {}, source_path: null,
      description: "修订次数上限。", optional: false,
      hook_details: {
        before_agent: { trigger: "本轮开始。", action: "重置本轮审查次数。", effect: "新一轮重新计数。" },
        wrap_tool_call: { trigger: "委托审稿助手。", action: "拦截超出上限的审查。", effect: "阻止继续委托。" },
      },
    }, {
      hook: null, hooks: [], class_name: "OldMiddleware", group: "runtime", params: {},
      source_path: null, description: null, optional: false,
    }];
    render(<MiddlewareTab agents={[main]} diffs={null} hasSource />);

    expect(screen.getAllByRole("heading", { level: 3 }).map((node) => node.textContent)).toEqual([
      "before_agent", "before_model", "wrap_model_call", "after_model", "wrap_tool_call", "after_agent",
    ]);
    expect(screen.getAllByRole("button", { name: /RevisionLimitMiddleware/ })).toHaveLength(2);
    expect(screen.getByText(/数据不足.*OldMiddleware/)).toBeTruthy();
    expect(screen.queryByText(/Agent 专属|运行时注入/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /重置本轮审查次数/ }));
    let detail = within(screen.getByRole("dialog"));
    expect(detail.getByText("触发条件")).toBeTruthy();
    expect(detail.getByText("本轮开始。")).toBeTruthy();
    expect(detail.getByText("新一轮重新计数。")).toBeTruthy();
    fireEvent.click(detail.getByRole("button", { name: "关闭详情" }));

    fireEvent.click(screen.getByRole("button", { name: /拦截超出上限的审查/ }));
    detail = within(screen.getByRole("dialog"));
    expect(detail.getByText("委托审稿助手。")).toBeTruthy();
    expect(detail.getByText("阻止继续委托。")).toBeTruthy();
  });

  it("区分旧版本中间件数据不足与已确认的空栈", () => {
    const role = agent("text", []);
    const { rerender } = render(<MiddlewareTab agents={[{ ...role, middleware_available: false }]} diffs={null} hasSource />);
    expect(screen.getByText(/数据不足/)).toBeTruthy();
    expect(screen.getAllByRole("heading", { level: 3 })).toHaveLength(6);

    rerender(<MiddlewareTab agents={[{ ...role, middleware_available: true }]} diffs={null} hasSource />);
    expect(screen.queryByText(/数据不足/)).toBeNull();
    expect(screen.getAllByRole("heading", { level: 3 })).toHaveLength(6);
  });
});
