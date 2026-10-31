/**
 * 执行状态卡 + 交付入口验收（REQ-20261012-202540 FR-004，AC-005/AC-006）。
 * 覆盖：默认一行、点开看时间线、完成自动收拢、交付卡「查看新作品」联动作品栏。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ExecutionView } from "@/components/workspace/ExecutionView";
import { DeliveryCeremony } from "@/components/workspace/execution/DeliveryCeremony";
import { useContentStore } from "@/stores/content";
import { useExecutionStore } from "@/stores/execution";
import { useWorkrailStore } from "@/stores/workrail";
import type { ChatMessage } from "@/lib/types";

function thinkingMessage(): ChatMessage {
  const t0 = Date.now() - 5000;
  return {
    role: "assistant",
    content: "",
    executionPhase: "thinking",
    thoughts: [{ id: 1, text: "第一行\n第二行\n", startedAt: t0, status: "streaming", label: "构思故事" }],
    tools: [{ key: "t1", name: "storybuilding", status: "running", startedAt: t0 }],
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  useWorkrailStore.setState({
    floatMode: "mini",
    pollError: false,
    highlightKeys: new Set(),
    baselineKeys: null,
    _highlightTimer: null,
  });
  useContentStore.setState({
    outlineFiles: [{ rel: "outline/00-赛道.md", markdown: "" }],
    characters: [],
    objects: [],
    worldviewMarkdown: "",
  });
});

describe("状态卡行为（AC-005）", () => {
  it("执行中默认一行：当前动作 + 耗时；时间线不可见", () => {
    render(<ExecutionView message={thinkingMessage()} loading isLastAssistant stageFlow={null} />);
    const line = screen.getByTestId("exec-status-line");
    expect(line.textContent).toContain("正在构思故事");
    expect(line.textContent).toContain("已");
    expect(screen.queryByTestId("yan-thought-text")).not.toBeInTheDocument();
  });

  it("点开显示思考时间线，再点收起", async () => {
    render(<ExecutionView message={thinkingMessage()} loading isLastAssistant stageFlow={null} />);
    await userEvent.click(screen.getByTestId("exec-status-line"));
    expect(screen.getAllByTestId("yan-thought-text").length).toBeGreaterThan(0);
    await userEvent.click(screen.getByTestId("exec-status-line"));
    expect(screen.queryByTestId("yan-thought-text")).not.toBeInTheDocument();
  });

  it("完成后自动折叠为「✓ 完成」摘要（用户展开态被收拢）", async () => {
    const { rerender } = render(
      <ExecutionView message={thinkingMessage()} loading isLastAssistant stageFlow={null} />,
    );
    await userEvent.click(screen.getByTestId("exec-status-line")); // 用户先展开
    const done: ChatMessage = {
      ...thinkingMessage(),
      executionPhase: "delivering",
      status: "completed",
      thoughts: [{ id: 1, text: "第一行\n", startedAt: Date.now() - 5000, endedAt: Date.now() - 1000, status: "done" }],
      tools: [{ key: "t1", name: "storybuilding", status: "done", startedAt: Date.now() - 5000, endedAt: Date.now() - 1000 }],
    };
    rerender(<ExecutionView message={done} loading={false} isLastAssistant stageFlow={null} />);
    const line = screen.getByTestId("exec-status-line");
    expect(line.textContent).toContain("✓ 完成");
    expect(screen.queryByTestId("yan-thought-text")).not.toBeInTheDocument();
  });
});

describe("交付卡产物入口（AC-006）", () => {
  it("「查看新作品」→ 展开作品栏并高亮本次新增", async () => {
    // 起点快照：只有 00-赛道；交付后新增 04-总纲 → 应高亮
    useWorkrailStore.getState().captureBaseline();
    useContentStore.setState({
      outlineFiles: [
        { rel: "outline/00-赛道.md", markdown: "" },
        { rel: "outline/04-总纲.md", markdown: "" },
      ],
    });

    render(<DeliveryCeremony message={{ role: "assistant", content: "" }} stageFlow={null} />);
    await userEvent.click(screen.getByRole("button", { name: "查看新作品" }));

    const rail = useWorkrailStore.getState();
    expect(rail.floatMode).toBe("expanded");
    expect([...rail.highlightKeys]).toContain("outline:outline/04-总纲.md");
    expect([...rail.highlightKeys]).not.toContain("outline:outline/00-赛道.md");
  });
});
