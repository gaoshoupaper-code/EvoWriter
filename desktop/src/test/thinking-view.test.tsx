/**
 * ThinkingView（图片流专用）组件测试
 *
 * DEC-006（REQ-20261010-182114）：本视图仅图片生成域使用
 * （activeStreamKind=image，图片后端无 reasoning_stream）；
 * 写作域思考态走 ThinkingTimeline（真实思考流时间线）。
 *
 * 覆盖：图片主题池文案、轮播防重复、展开步骤列表。
 * （原写作域文案轮播用例随视图分工迁移至 thinking-timeline.test.tsx 语义）
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { act } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { ThinkingView } = await import("@/components/workspace/execution/ThinkingView");
const { THINKING_COPY, TOOL_DISPLAY_NAMES } = await import("@/lib/yan-copy");

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

describe("ThinkingView（图片流，DEC-006）", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("文案来自图片主题池（streamKind 固定 image）", () => {
    render(<ThinkingView message={makeMessage([{ subagentType: "writing" }])} stageFlow={null} />);
    // 即便误传写作域工具信号，图片流视图也必须用图片文案池
    expect(THINKING_COPY.image).toContain(thinkingText());
  });

  it("同主题超 7 秒换句，不重复最近 2 句；渲染本身不换句", () => {
    vi.useFakeTimers();
    const { rerender } = render(
      <ThinkingView message={makeMessage([])} stageFlow={null} />,
    );
    // fake timers 冻结了 React 调度 passive effect 用的 setTimeout(0)：先冲刷再取基线
    act(() => { vi.advanceTimersByTime(0); });

    const first = thinkingText();
    expect(THINKING_COPY.image).toContain(first);

    rerender(<ThinkingView message={makeMessage([])} stageFlow={null} />);
    expect(thinkingText()).toBe(first);

    act(() => { vi.advanceTimersByTime(8000); });
    const second = thinkingText();
    expect(second).not.toBe(first);
    expect(THINKING_COPY.image).toContain(second);
  });

  it("展开显示步骤列表——当前动作耗时、最近步明细、失败步标红", async () => {
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

    const current = document.querySelector(".yan-thinking-steps-current")?.textContent ?? "";
    expect(current).toContain(TOOL_DISPLAY_NAMES.meta);
    expect(current).toContain("已");

    const steps = document.querySelectorAll(".yan-thinking-step");
    expect(steps.length).toBe(4);
    const failedStep = document.querySelector('.yan-thinking-step[data-status="failed"]');
    expect(failedStep).not.toBeNull();
    expect(failedStep?.textContent).toContain("✗");

    expect(document.querySelector('.yan-thinking-step[data-status="done"]')?.textContent).toMatch(/\d+ 秒/);
    expect(document.querySelector('.yan-thinking-step[data-status="running"]')?.textContent).toContain("进行中");
  });

  it("tools 为空时展开显示当前阶段文案，不报错", async () => {
    const user = userEvent.setup();
    render(<ThinkingView message={makeMessage([])} stageFlow={null} />);
    await user.click(document.querySelector(".yan-thinking-summary")!);
    expect(document.querySelector(".yan-thinking-placeholder")).not.toBeNull();
  });
});
