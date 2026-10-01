/**
 * REQ-20261001-154621 AC-005/AC-006/AC-008：思考态动态文案
 *
 * FR-004 工具信号驱动：有工具信号按映射表显示；信号变化文案同步变；
 *   未覆盖工具名降级「工具人话名进行中...」
 * FR-005 轮播防重复：同主题超 7 秒换句、不重复最近 2 句；渲染不重抽
 * FR-007 图片流文案：activeStreamKind=image 时文案来自图片主题池
 *
 * 组件级测试：直接渲染 ThinkingView，注入 message.tools 与 store 瞬态。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { ThinkingView } = await import("@/components/workspace/execution/ThinkingView");
const { THINKING_COPY, TOOL_DISPLAY_NAMES } = await import("@/lib/yan-copy");
const { useExecutionStore } = await import("@/stores/execution");

function thinkingText() {
  return document.querySelector(".yan-thinking-text")?.textContent ?? "";
}

function makeMessage(tools: Array<Record<string, unknown>>) {
  return {
    role: "assistant" as const,
    content: "正文内容",
    tools: tools.map((t, i) => ({ key: `call-${i}`, name: "task", status: "running" as const, ...t })),
  };
}

describe("AC-005/006/008: 思考态动态文案", () => {
  beforeEach(() => {
    useExecutionStore.setState({ activeReasoning: "", activeStreamKind: "" });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("AC-005: writing 信号 → 文案来自 writing 主题池", () => {
    render(<ThinkingView message={makeMessage([{ subagentType: "writing", subagentName: "writing" }])} stageFlow={null} />);
    expect(THINKING_COPY.writing).toContain(thinkingText());
  });

  it("AC-005: 信号从 storybuilding 变 writing → 文案同步切池", () => {
    const { rerender } = render(
      <ThinkingView message={makeMessage([{ subagentType: "storybuilding", iteration: 1 }])} stageFlow={null} />,
    );
    expect(THINKING_COPY.storybuilding).toContain(thinkingText());

    rerender(
      <ThinkingView message={makeMessage([{ subagentType: "writing" }])} stageFlow={null} />,
    );
    expect(THINKING_COPY.writing).toContain(thinkingText());
  });

  it("AC-005: 映射表未覆盖的工具名 → 降级「工具人话名进行中...」", () => {
    render(<ThinkingView message={makeMessage([{ name: "propose_evolution_point" }])} stageFlow={null} />);
    const text = thinkingText();
    expect(text).toContain("进行中");
    expect(text).toContain(TOOL_DISPLAY_NAMES.propose_evolution_point ?? "propose_evolution_point");
  });

  it("AC-005: 构思第 2+ 轮显示轮次后缀", () => {
    render(<ThinkingView message={makeMessage([{ subagentType: "storybuilding", iteration: 2 }])} stageFlow={null} />);
    expect(thinkingText()).toMatch(/（第 2 轮）/);
  });

  it("AC-006: 同主题超 7 秒换句，不重复最近 2 句；渲染本身不换句", () => {
    vi.useFakeTimers();
    const { rerender } = render(
      <ThinkingView message={makeMessage([{ subagentType: "writing" }])} stageFlow={null} />,
    );
    // fake timers 冻结了 React 调度 passive effect 用的 setTimeout(0)：先冲刷再取基线
    act(() => { vi.advanceTimersByTime(0); });

    const first = thinkingText();
    expect(THINKING_COPY.writing).toContain(first);

    // 强制重渲染（新 props 对象）不推进时间：文案不换（消灭 render-per-random 机制）
    rerender(<ThinkingView message={makeMessage([{ subagentType: "writing" }])} stageFlow={null} />);
    expect(thinkingText()).toBe(first);

    act(() => { vi.advanceTimersByTime(8000); });
    const second = thinkingText();
    expect(second).not.toBe(first);
    expect(THINKING_COPY.writing).toContain(second);

    act(() => { vi.advanceTimersByTime(8000); });
    const third = thinkingText();
    expect(third).not.toBe(second);
    expect(third).not.toBe(first); // 不与最近 2 句重复
    expect(THINKING_COPY.writing).toContain(third);
  });

  it("AC-008: 图片流（activeStreamKind=image）→ 文案来自图片主题池，不含「正在思考」", () => {
    useExecutionStore.setState({ activeStreamKind: "image" });
    render(<ThinkingView message={makeMessage([])} stageFlow={null} />);
    const text = thinkingText();
    expect(THINKING_COPY.image).toContain(text);
    expect(text).not.toContain("正在思考");
  });

  it("AC-007: 展开显示步骤列表——当前动作耗时、最近步明细、失败步标红", async () => {
    const user = userEvent.setup();
    const now = Date.now();
    const message = makeMessage([
      { key: "call-0", name: "task", subagentType: "storybuilding", status: "done", startedAt: now - 60000, endedAt: now - 50000 },
      { key: "call-1", name: "task", subagentType: "writing", status: "failed", startedAt: now - 40000, endedAt: now - 30000 },
      { key: "call-2", name: "task", subagentType: "review", status: "done", startedAt: now - 20000, endedAt: now - 10000 },
      { key: "call-3", name: "task", subagentType: "meta", status: "running", startedAt: now - 5000 },
    ]);
    render(<ThinkingView message={message} stageFlow={null} />);

    await user.click(document.querySelector(".yan-thinking-summary")!);

    // 当前动作：running 步 + 已耗时
    const current = document.querySelector(".yan-thinking-steps-current")?.textContent ?? "";
    expect(current).toContain(TOOL_DISPLAY_NAMES.meta);
    expect(current).toContain("已");

    // 步骤明细：4 步都在，失败步带 ✗ 与 data-status=failed
    const steps = document.querySelectorAll(".yan-thinking-step");
    expect(steps.length).toBe(4);
    const failedStep = document.querySelector('.yan-thinking-step[data-status="failed"]');
    expect(failedStep).not.toBeNull();
    expect(failedStep?.textContent).toContain("✗");

    // 完成步显示耗时、运行中步显示「进行中」
    expect(document.querySelector('.yan-thinking-step[data-status="done"]')?.textContent).toMatch(/\d+ 秒/);
    expect(document.querySelector('.yan-thinking-step[data-status="running"]')?.textContent).toContain("进行中");
  });

  it("AC-007 边界: 超过 5 步只显示最近 5 步", async () => {
    const user = userEvent.setup();
    const now = Date.now();
    const tools: Array<Record<string, unknown>> = Array.from({ length: 7 }, (_, i) => ({
      key: `call-${i}`, name: "task", status: "done", startedAt: now - 1000, endedAt: now,
    }));
    tools.push({ key: "call-run", name: "task", status: "running", startedAt: now - 1000 });
    render(<ThinkingView message={makeMessage(tools)} stageFlow={null} />);
    await user.click(document.querySelector(".yan-thinking-summary")!);
    expect(document.querySelectorAll(".yan-thinking-step").length).toBe(5);
  });

  it("AC-007 边界: tools 为空时展开显示当前阶段文案，不报错", async () => {
    const user = userEvent.setup();
    render(<ThinkingView message={makeMessage([])} stageFlow={null} />);
    await user.click(document.querySelector(".yan-thinking-summary")!);
    expect(document.querySelector(".yan-thinking-placeholder")?.textContent).toMatch(/小衍正在/);
  });

  it("AC-007: deepseek 有 reasoning 时展开保留 reasoning 文本", async () => {
    useExecutionStore.setState({ activeReasoning: "用户想要一个关于勇气的推理文本" });
    const user = userEvent.setup();
    render(<ThinkingView message={makeMessage([{ subagentType: "writing" }])} stageFlow={null} />);
    await user.click(document.querySelector(".yan-thinking-summary")!);
    expect(document.querySelector(".yan-thinking-reasoning")?.textContent).toContain("推理文本");
  });
});
