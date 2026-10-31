/**
 * 对话区初始滚底验收（用户反馈：进入对话页面应直接显示最后一条）。
 * jsdom 无真实布局——scrollHeight 恒 0，这里 mock 原型属性给出固定值，
 * 断言 scrollTop 被设置到 scrollHeight（挂载即到底 / 消息异步到达再滚一次 /
 * 流式追加不打断用户回看）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { ChatPanel } from "@/components/workspace/ChatPanel";
import type { ChatMessage } from "@/lib/types";

const SCROLL_HEIGHT = 500;

function mockScrollHeight() {
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
    configurable: true,
    get() {
      return SCROLL_HEIGHT;
    },
  });
}

function baseProps() {
  return {
    prompt: "",
    loading: false,
    activeThreadId: "thread-1",
    hasActiveWorkspace: true,
    onPromptChange: vi.fn(),
    onSubmit: vi.fn(),
    onResumeSubmit: vi.fn(),
    onStop: vi.fn(),
    onDeleteThread: vi.fn(),
  };
}

const history: ChatMessage[] = [
  { role: "user", content: "写第一章" },
  { role: "assistant", content: "好的，这是第一章。" },
  { role: "user", content: "继续" },
  { role: "assistant", content: "第二章来了。" },
];

describe("进入对话页即显示最后一条", () => {
  beforeEach(() => {
    mockScrollHeight();
  });
  afterEach(() => {
    delete (HTMLElement.prototype as { scrollHeight?: number }).scrollHeight;
  });

  it("挂载时已有历史消息 → 滚到底部", () => {
    render(<ChatPanel {...baseProps()} messages={history} />);
    const list = document.querySelector(".message-list") as HTMLElement;
    expect(list.scrollTop).toBe(SCROLL_HEIGHT);
  });

  it("挂载时消息未加载（空）→ 消息到达后补滚到底部", () => {
    const { rerender } = render(<ChatPanel {...baseProps()} messages={[]} />);
    const list = document.querySelector(".message-list") as HTMLElement;
    expect(list.scrollTop).toBe(SCROLL_HEIGHT); // 挂载先滚一次（空列表）

    list.scrollTop = 0; // 模拟消息未到、用户停顶部
    rerender(<ChatPanel {...baseProps()} messages={history} />);
    expect(list.scrollTop).toBe(SCROLL_HEIGHT); // 0→N 触发补滚
  });

  it("流式追加（非 0→N）不强制滚底，不打断回看", () => {
    const { rerender } = render(<ChatPanel {...baseProps()} messages={history} />);
    const list = document.querySelector(".message-list") as HTMLElement;
    list.scrollTop = 120; // 用户上翻回看
    rerender(<ChatPanel {...baseProps()} messages={[...history, { role: "assistant", content: "流式…" }]} />);
    expect(list.scrollTop).toBe(120);
  });
});
