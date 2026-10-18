/**
 * RowTimeline + ExecutionView 行渲染验收（REQ-20261018-235653，AC-001/FR-005/FR-008/AC-005）。
 *
 * - 交错完整性：思考/正文/工具/子代理按行序渲染，正文块常驻可见（AC-001 渲染侧）；
 * - 子代理聚合行：展开才见摘要（FR-005 渲染侧）；
 * - 历史消息同形态：非最后一条 assistant 的行消息也走状态卡 + 时间线（FR-008）；
 * - 双轨：无 rows 的旧消息仍走 ThinkingTimeline（FR-007/AC-005 渲染侧）。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RowTimeline } from "@/components/workspace/execution/RowTimeline";
import { ExecutionView } from "@/components/workspace/ExecutionView";
import { useContentStore } from "@/stores/content";
import { useExecutionStore } from "@/stores/execution";
import { useWorkrailStore } from "@/stores/workrail";
import type { ChatMessage, TimelineRow } from "@/lib/types";

const T0 = Date.now() - 60_000;

function mixedRows(): TimelineRow[] {
  return [
    { seq: 1, id: "r1", kind: "reasoning", text: "思考第一段", status: "complete", startedAt: T0, endedAt: T0 + 2000 },
    { seq: 2, id: "r2", kind: "text", text: "**确认卡**全文", status: "complete", startedAt: T0 + 2000, endedAt: T0 + 4000 },
    { seq: 3, id: "c1", kind: "tool", toolName: "write_file", status: "complete", startedAt: T0 + 4000, endedAt: T0 + 5000 },
    { seq: 4, id: "t1", kind: "subagent", subagentType: "storybuilding", iteration: 1, status: "complete", summary: "第 1 轮产出概要", startedAt: T0 + 5000, endedAt: T0 + 20000 },
    { seq: 5, id: "r5", kind: "text", text: "收尾一句", status: "complete", startedAt: T0 + 20000, endedAt: T0 + 22000 },
  ];
}

function rowMessage(rows: TimelineRow[], phase: ChatMessage["executionPhase"] = "delivering"): ChatMessage {
  return {
    role: "assistant",
    content: "**确认卡**全文\n\n收尾一句",
    contentFormat: "markdown",
    rows,
    status: "completed",
    executionPhase: phase,
    tools: [],
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  useExecutionStore.setState({ activeStreamKind: "writing" });
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

describe("RowTimeline 交错渲染（AC-001 渲染侧）", () => {
  it("四类行按行序渲染，正文块常驻可见、思考块终态默认折叠", () => {
    render(<RowTimeline rows={mixedRows()} />);

    const timeline = screen.getByTestId("row-timeline");
    const children = Array.from(timeline.children);
    // 顺序 = 行序：思考块 → 正文块 → 工具行 → 子代理行 → 正文块
    expect(children.map((el) => el.className)).toEqual([
      "yan-thought-row",
      "yan-text-row",
      "yan-tool-row",
      "yan-subagent-row",
      "yan-text-row",
    ]);

    // 正文块常驻可见（含中间正文，FR-003）
    expect(screen.getAllByTestId("yan-text-row-r2")[0].textContent).toContain("确认卡");
    expect(screen.getByTestId("yan-text-row-r5").textContent).toContain("收尾一句");

    // 终态思考块默认折叠：正文不可见（展开行为由下一用例覆盖）
    expect(screen.queryByTestId("row-thought-text")).not.toBeInTheDocument();
  });

  it("思考块点开显示正文（fireAndForget 避免_act 警告）", async () => {
    render(<RowTimeline rows={mixedRows()} />);
    await userEvent.click(screen.getByTestId("row-thought-trigger-r1"));
    expect(screen.getByTestId("row-thought-text").textContent).toContain("思考第一段");
  });

  it("子代理聚合行：默认只见单行，展开才见摘要（FR-005 渲染侧）", async () => {
    render(<RowTimeline rows={mixedRows()} />);
    const trigger = screen.getByTestId("row-subagent-t1");
    // subagent_type 经 TOOL_DISPLAY_NAMES 映射为人话名（构思故事）
    expect(trigger.textContent).toContain("构思故事");
    expect(trigger.textContent).toContain("第 1 轮");
    expect(screen.queryByTestId("row-subagent-summary-t1")).not.toBeInTheDocument();
    await userEvent.click(trigger);
    expect(screen.getByTestId("row-subagent-summary-t1").textContent).toContain("第 1 轮产出概要");
  });
});

describe("ExecutionView 行消息选路（FR-003/FR-007/FR-008）", () => {
  it("行消息：收起时最终正文常显，展开见完整交错时间线（历史消息同形态，FR-008）", async () => {
    // 非最后一条 assistant（历史消息）——HistoryFolded 已退役，同形态渲染
    render(<ExecutionView message={rowMessage(mixedRows())} loading={false} isLastAssistant={false} stageFlow={null} />);

    // 末块正文外置常显：不展开状态卡也能看到最终回答
    expect(screen.getByTestId("yan-text-row-r5").textContent).toContain("收尾一句");
    // 展开后：卡内时间线含中间正文/思考/工具/子代理（按行序交错）
    await userEvent.click(screen.getByTestId("exec-status-line"));
    expect(screen.getAllByTestId("yan-text-row-r2").length).toBeGreaterThan(0);
    expect(screen.getByTestId("row-tool-c1")).toBeInTheDocument();
    expect(screen.getByTestId("row-subagent-t1")).toBeInTheDocument();
  });

  it("运行中默认展开：时间线实时生长，不需点击（FR-003）", () => {
    const msg = rowMessage(mixedRows(), "thinking");
    render(<ExecutionView message={msg} loading isLastAssistant stageFlow={null} />);
    expect(screen.getByTestId("row-thought-trigger-r1")).toBeInTheDocument();
    expect(screen.getByTestId("yan-text-row-r2")).toBeInTheDocument();
  });

  it("提问中断不收拢：asking 态时间线默认展开（FR-004）", () => {
    const msg: ChatMessage = {
      ...rowMessage(mixedRows(), "asking"),
      awaitingInput: {
        kind: "choice", question: "确认这个版本吗？", options: [], multi_select: false,
        askedAt: new Date().toISOString(),
      },
    };
    render(<ExecutionView message={msg} loading={false} isLastAssistant stageFlow={null} />);
    expect(screen.getByTestId("row-thought-trigger-r1")).toBeInTheDocument();
  });

  it("双轨（FR-007/AC-005 渲染侧）：无 rows 的旧消息仍走 ThinkingTimeline", async () => {
    const legacy: ChatMessage = {
      role: "assistant",
      content: "旧正文",
      thoughts: [{ id: 1, text: "旧思考行\n", startedAt: T0, status: "done", endedAt: T0 + 1000 }],
      tools: [],
      status: "completed",
      executionPhase: "delivering",
    };
    render(<ExecutionView message={legacy} loading={false} isLastAssistant={false} stageFlow={null} />);
    await userEvent.click(screen.getByTestId("exec-status-line"));
    expect(screen.queryByTestId("row-timeline")).not.toBeInTheDocument();
    expect(document.querySelector(".yan-thought-timeline")).toBeInTheDocument();
  });
});
