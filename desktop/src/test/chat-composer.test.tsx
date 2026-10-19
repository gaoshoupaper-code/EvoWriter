import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ChatPanel } from "@/components/workspace/ChatPanel";
import { useStageSessionStore } from "@/stores/stage-session";

function props(loading = false) {
  return {
    messages: [], prompt: "继续写作", loading, activeThreadId: "t1", hasActiveWorkspace: true,
    onPromptChange: vi.fn(), onSubmit: vi.fn((event: React.FormEvent) => event.preventDefault()),
    onResumeSubmit: vi.fn(), onImageReviewSubmit: vi.fn(), onStop: vi.fn(), onDeleteThread: vi.fn(),
  };
}

beforeEach(() => useStageSessionStore.setState({ historyView: null, pendingTransition: null }));

describe("输入气泡（REQ-20261018-202005 AC-004）", () => {
  it("发送图标与文本框在同一个气泡内", () => {
    const p = props();
    const { container } = render(<ChatPanel {...p} />);
    const send = screen.getByRole("button", { name: "发送" });
    expect(send.closest(".chat-input-wrap")).toBe(screen.getByRole("textbox").closest(".chat-input-wrap"));
    expect(send.querySelector("svg")).not.toBeNull();
    expect(container.querySelectorAll(".send-button, .stop-button")).toHaveLength(1);
    fireEvent.click(send);
    expect(p.onSubmit).toHaveBeenCalledOnce();
  });

  it("生成时只显示停止方块，点击不会提交表单；输入框保持可输入（FR-006 排队）", () => {
    const p = props(true);
    const { container, rerender } = render(<ChatPanel {...p} />);
    const stop = screen.getByRole("button", { name: "停止" });
    expect(container.querySelectorAll(".send-button, .stop-button")).toHaveLength(1);
    expect(stop.querySelector("svg")).not.toBeNull();
    expect(stop).toHaveAttribute("type", "button");
    // REQ-20261019-222403 FR-006：生成中不再禁发——可输入排队，只有拍板中禁用
    expect(screen.getByRole("textbox")).toBeEnabled();
    fireEvent.click(stop);
    expect(p.onStop).toHaveBeenCalledOnce();
    expect(p.onSubmit).not.toHaveBeenCalled();
    rerender(<ChatPanel {...p} loading={false} />);
    expect(screen.getByRole("button", { name: "发送" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "停止" })).toBeNull();
  });

  it("保留 Enter、Shift+Enter 与中文输入法规则", () => {
    const p = props();
    render(<ChatPanel {...p} />);
    const input = screen.getByRole("textbox");
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    expect(p.onSubmit).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(p.onSubmit).toHaveBeenCalledOnce();
  });

  it("生成中 Enter 排队发送（FR-006：同会话消息排队串行）", () => {
    const p = props(true);
    render(<ChatPanel {...p} />);
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
    expect(p.onSubmit).toHaveBeenCalledOnce();
  });
});
