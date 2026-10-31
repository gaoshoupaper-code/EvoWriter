/**
 * 创作双模式前端验收（REQ-20261026-191719 AC-001/005 前端侧）。
 *
 * - review 停等卡渲染：interrupt kind="review" → ReviewCard（批次/文风/段审）
 * - 违纪代答灰条：autoAnswers 累积渲染（FR-007 事后可回查）
 * - 模式开关锁定：题材/总纲段置灰不可点（DEC-002）
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatPanel } from "@/components/workspace/ChatPanel";
import { useStageSessionStore } from "@/stores/stage-session";
import { useContentStore } from "@/stores/content";
import { useWorkspaceStore } from "@/stores/workspace";
import type { ChatMessage } from "@/lib/types";

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

function reviewMessage(reviewKind: "batch" | "style" | "stage"): ChatMessage {
  return {
    role: "assistant",
    content: "批次完成",
    status: "streaming",
    awaitingInput: {
      kind: "review",
      review_kind: reviewKind,
      question: "第 1-5 章已写完，请审查",
      options: null,
      multi_select: false,
      askedAt: new Date().toISOString(),
    },
  };
}

describe("ModeToggle 模式开关", () => {
  const setPipeline = (phase: string) => {
    useContentStore.setState({
      outlinePipeline: { kind: "five", phase, ready: { ready: false, next: null }, awaiting: {} },
    } as never);
  };

  it("题材/总纲段锁死逐步（AC-001）", () => {
    setPipeline("theme");
    useStageSessionStore.setState({ stageModes: { theme: "interactive" }, autoEligibleStages: ["volume", "chapter", "text"] });
    const { rerender } = render(<ChatPanel {...baseProps()} messages={[{ role: "user", content: "hi" }]} />);
    const toggle = screen.getByTitle(/固定逐步确认/) as HTMLButtonElement;
    expect(toggle.disabled).toBe(true);
    setPipeline("outline");
    rerender(<ChatPanel {...baseProps()} messages={[{ role: "user", content: "hi" }]} />);
    expect((screen.getByTitle(/固定逐步确认/) as HTMLButtonElement).disabled).toBe(true);
  });

  it("可自动段显示当前段模式并可切换（AC-001）", async () => {
    setPipeline("volume");
    useStageSessionStore.setState({
      stageModes: { volume: "interactive" },
      autoEligibleStages: ["volume", "chapter", "text"],
      toggleStageMode: vi.fn(),
    });
    render(<ChatPanel {...baseProps()} messages={[{ role: "user", content: "hi" }]} />);
    const toggle = screen.getByTitle(/点击切到自动完成/) as HTMLButtonElement;
    expect(toggle.textContent).toContain("逐步确认");
    expect(toggle.disabled).toBe(false);
  });
});

describe("ReviewCard 停等卡", () => {
  beforeEach(() => {
    useContentStore.setState({ outlinePipeline: null });
    useStageSessionStore.setState({
      stageModes: null,
      autoEligibleStages: [],
      batchSize: 5,
      lastBatchUsage: null,
    });
  });

  it("批次停等卡：继续 N 章 + 打回 + 消耗占位（AC-005）", async () => {
    const props = baseProps();
    render(<ChatPanel {...props} messages={[{ role: "user", content: "继续" }, reviewMessage("batch")]} />);
    const card = await screen.findByTestId("review-card");
    expect(card).toBeTruthy();
    // 三要素：继续 N 章 / 打回 / 消耗（无数据占位不报错）
    expect(screen.getByText(/继续写/)).toBeTruthy();
    expect(screen.getByText("打回修改")).toBeTruthy();
    expect(screen.getByText(/上一批消耗：暂无数据/)).toBeTruthy();
    // 提交续写：resume 文本携带 N（默认 5）
    await userEvent.click(screen.getByRole("button", { name: /继续写 5 章/ }));
    expect(props.onResumeSubmit).toHaveBeenCalledWith("继续写 5 章");
  });

  it("文风停等卡：确认通过 + 打回意见（AC-007 前端侧）", async () => {
    const props = baseProps();
    render(<ChatPanel {...props} messages={[reviewMessage("style")]} />);
    await userEvent.click(await screen.findByRole("button", { name: "确认通过" }));
    expect(props.onResumeSubmit).toHaveBeenCalledWith("确认通过");
  });

  it("违纪代答灰条渲染（AC-003 前端侧）", () => {
    const msg: ChatMessage = {
      role: "assistant",
      content: "写作中",
      autoAnswers: [
        { question: "选 A 还是 B", answer: "由你自行决定", at: new Date().toISOString() },
      ],
    };
    render(<ChatPanel {...baseProps()} messages={[msg]} />);
    expect(screen.getByTestId("auto-answer-list").textContent).toContain("选 A 还是 B");
  });
});

describe("ReviewCard 段审确认循环推进（2026-10-27 卷纲连写 bug 修复）", () => {
  beforeEach(() => {
    useContentStore.setState({ outlinePipeline: null });
    useStageSessionStore.setState({
      stageModes: null,
      autoEligibleStages: [],
      batchSize: 5,
      lastBatchUsage: null,
    });
    useWorkspaceStore.setState({ activeWorkspaceId: "ws-1" } as never);
  });

  it("段审卡确认通过：先完成会话切换，再提交新段开工", async () => {
    const advance = vi.fn().mockResolvedValue(true);
    useStageSessionStore.setState({ confirmStageReviewAdvance: advance });
    const props = baseProps();
    const message = reviewMessage("stage");
    message.traceId = "trace-shared";
    message.awaitingInput!.review_id = "review-volume";
    const view = render(<ChatPanel {...props} messages={[message]} />);
    await userEvent.click(await screen.findByRole("button", { name: "确认通过" }));
    expect(advance).toHaveBeenCalledWith("ws-1", "trace-shared", "review-volume");
    expect(props.onResumeSubmit).toHaveBeenCalledWith("确认通过");
    expect(advance.mock.invocationCallOrder[0]).toBeLessThan(props.onResumeSubmit.mock.invocationCallOrder[0]);

    view.unmount();
    const nextMessage = reviewMessage("stage");
    nextMessage.traceId = "trace-chapter";
    nextMessage.awaitingInput!.review_id = "review-chapter";
    render(<ChatPanel {...props} messages={[nextMessage]} />);
    await userEvent.click(await screen.findByRole("button", { name: "确认通过" }));
    expect(advance).toHaveBeenNthCalledWith(2, "ws-1", "trace-chapter", "review-chapter");
  });

  it("段审卡推进失败：不续跑，卡片解锁可重试", async () => {
    const advance = vi.fn().mockResolvedValue(false);
    useStageSessionStore.setState({ confirmStageReviewAdvance: advance });
    const props = baseProps();
    render(<ChatPanel {...props} messages={[reviewMessage("stage")]} />);
    await userEvent.click(await screen.findByRole("button", { name: "确认通过" }));
    expect(advance).toHaveBeenCalled();
    expect(props.onResumeSubmit).not.toHaveBeenCalled();
    // 解锁重试：再次点击仍走完整链路
    await userEvent.click(screen.getByRole("button", { name: "确认通过" }));
    expect(advance).toHaveBeenCalledTimes(2);
  });

  it("段审卡打回：不推进段，直接 resume 意见", async () => {
    const advance = vi.fn().mockResolvedValue(true);
    useStageSessionStore.setState({ confirmStageReviewAdvance: advance });
    const props = baseProps();
    render(<ChatPanel {...props} messages={[reviewMessage("stage")]} />);
    await userEvent.click(await screen.findByRole("button", { name: "打回修改" }));
    const textarea = await screen.findByPlaceholderText(/打回意见/);
    await userEvent.type(textarea, "钩子不够劲");
    await userEvent.click(screen.getByRole("button", { name: "提交打回" }));
    expect(advance).not.toHaveBeenCalled();
    expect(props.onResumeSubmit).toHaveBeenCalledWith("打回：钩子不够劲");
  });

  it("文风卡确认通过：不推进段（留在章纲段继续）", async () => {
    const advance = vi.fn().mockResolvedValue(true);
    useStageSessionStore.setState({ confirmStageReviewAdvance: advance });
    const props = baseProps();
    render(<ChatPanel {...props} messages={[reviewMessage("style")]} />);
    await userEvent.click(await screen.findByRole("button", { name: "确认通过" }));
    expect(advance).not.toHaveBeenCalled();
    expect(props.onResumeSubmit).toHaveBeenCalledWith("确认通过");
  });
});
