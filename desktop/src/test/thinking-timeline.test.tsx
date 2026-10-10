/**
 * ThinkingTimeline（思考流时间线）组件测试
 * REQ-20261010-182114：
 * - FR-002/AC-001：streaming 段折叠行「正在思考 + 最新一行」
 * - FR-003/AC-002：思考段与工具行按时间交替；段带子任务名
 * - FR-004/AC-004：展开态按自然换行一句一段（纯文本）
 * - FR-005/AC-005：中断段标「思考中断」；完成段「思考 · N 秒」
 * - FR-002 兜底/AC-003：无思考流时显示工具名 + 已耗时
 */
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ChatMessage, ThoughtSegment, ToolStatus } from "@/lib/types";
import { ThinkingTimeline } from "@/components/workspace/execution/ThinkingTimeline";

const NOW = 1_700_000_000_000;

function thought(over: Partial<ThoughtSegment>): ThoughtSegment {
  return { id: 1, text: "line", startedAt: NOW, status: "streaming", ...over };
}

function tool(over: Partial<ToolStatus>): ToolStatus {
  return { key: "call-0", name: "task", status: "running", startedAt: NOW, ...over };
}

function message(over: Partial<ChatMessage>): ChatMessage {
  return { role: "assistant", content: "正在执行...", ...over };
}

describe("FR-003/AC-002：时间线分段与工具行交替", () => {
  it("思考段与工具行按 startedAt 交替渲染", () => {
    const m = message({
      thoughts: [
        thought({ id: 1, text: "先想主线", startedAt: NOW, status: "done", endedAt: NOW + 4000, label: "构思故事" }),
        thought({ id: 2, text: "再想人物", startedAt: NOW + 10000, status: "streaming", label: "正文写作" }),
      ],
      tools: [tool({ key: "t1", name: "task", startedAt: NOW + 5000, status: "done", endedAt: NOW + 9000 })],
    });
    const { container } = render(<ThinkingTimeline message={m} loading />);

    const rows = Array.from(container.querySelectorAll(".yan-thought-timeline > *"));
    expect(rows.map((r) => r.className)).toEqual([
      "yan-thought-row",   // 思考段 1（t=NOW）
      "yan-tool-row",      // 工具行（t=NOW+5s）
      "yan-thought-row",   // 思考段 2（t=NOW+10s）
    ]);
  });

  it("思考段带子任务名 label（FR-003）", () => {
    const m = message({ thoughts: [thought({ label: "构思故事" })] });
    render(<ThinkingTimeline message={m} loading />);
    expect(document.querySelector(".yan-thought-label")?.textContent).toBe("构思故事");
  });
});

describe("FR-002/AC-001：streaming 段折叠行实时摘要", () => {
  it("折叠行显示「正在思考」+ 思考文本最新一行（DEC-008 英文原文直出）", () => {
    const m = message({
      thoughts: [thought({ text: "The user wants a story.\nLet me think about the twist." })],
    });
    render(<ThinkingTimeline message={m} loading />);

    const trigger = document.querySelector(".yan-thought-trigger")!;
    expect(trigger.textContent).toContain("正在思考");
    const live = document.querySelector(".yan-thought-live")!;
    expect(live.textContent).toBe("Let me think about the twist.");
  });

  it("文本增长后摘要行跟随最新一行", () => {
    const m = message({ thoughts: [thought({ text: "first line" })] });
    const { rerender } = render(<ThinkingTimeline message={m} loading />);
    expect(document.querySelector(".yan-thought-live")?.textContent).toBe("first line");

    m.thoughts = [thought({ text: "first line\nsecond line" })];
    rerender(<ThinkingTimeline message={m} loading />);
    expect(document.querySelector(".yan-thought-live")?.textContent).toBe("second line");
  });
});

describe("FR-004/AC-004：展开态一句一段", () => {
  it("点开段落后按自然换行分段渲染，空行跳过", async () => {
    const user = userEvent.setup();
    const m = message({
      thoughts: [thought({
        text: "First idea about the hero.\n\nSecond idea about the villain.",
        status: "done", endedAt: NOW + 12000,
      })],
    });
    render(<ThinkingTimeline message={m} loading={false} />);

    await user.click(document.querySelector(".yan-thought-trigger")!);
    const paras = document.querySelectorAll(".yan-thought-para");
    expect(paras.length).toBe(2);
    expect(paras[0].textContent).toBe("First idea about the hero.");
    expect(paras[1].textContent).toBe("Second idea about the villain.");
  });

  it("完成段折叠行显示「思考 · N 秒」", () => {
    const m = message({
      thoughts: [thought({ text: "x", status: "done", startedAt: NOW, endedAt: NOW + 12000 })],
    });
    render(<ThinkingTimeline message={m} loading={false} />);
    expect(document.querySelector(".yan-thought-done-title")?.textContent).toContain("12 秒");
  });
});

describe("FR-005/AC-005：中断标记", () => {
  it("interrupted 段显示「思考中断」", () => {
    const m = message({
      thoughts: [thought({ text: "想了一半", status: "interrupted", endedAt: NOW + 5000 })],
    });
    render(<ThinkingTimeline message={m} loading={false} />);
    expect(document.querySelector(".yan-thought-interrupted")?.textContent).toBe("思考中断");
  });

  it("中断段与完成段共存：完成在前中断在后，均可展开", async () => {
    const user = userEvent.setup();
    const m = message({
      thoughts: [
        thought({ id: 1, text: "完整的一段", status: "done", startedAt: NOW, endedAt: NOW + 3000 }),
        thought({ id: 2, text: "断在这里", status: "interrupted", startedAt: NOW + 4000, endedAt: NOW + 6000 }),
      ],
    });
    render(<ThinkingTimeline message={m} loading={false} />);

    const titles = document.querySelectorAll(".yan-thought-done-title, .yan-thought-interrupted");
    expect(titles[0]?.textContent).toContain("思考");
    expect(titles[1]?.textContent).toBe("思考中断");

    await user.click(document.querySelector('[data-testid="yan-thought-trigger-2"]')!);
    expect(document.querySelectorAll(".yan-thought-para")[0]?.textContent).toBe("断在这里");
  });
});

describe("FR-002 兜底/AC-003：无思考流显示真实进度", () => {
  it("无思考流 + running 工具 → 「正在思考 · 工具名 · 已 N 秒」", () => {
    const m = message({
      tools: [tool({ key: "t1", name: "task", subagentType: "storybuilding", startedAt: Date.now() - 5000 })],
    });
    render(<ThinkingTimeline message={m} loading />);
    const text = document.querySelector(".yan-thought-fallback-text")?.textContent ?? "";
    expect(text).toContain("正在思考");
    expect(text).toContain("构思故事");
    expect(text).toMatch(/已 \d+ 秒/);
  });

  it("无思考流无工具 → 仅「正在思考」，不空白", () => {
    render(<ThinkingTimeline message={message({})} loading />);
    expect(document.querySelector(".yan-thought-fallback-text")?.textContent).toContain("正在思考");
  });

  it("历史消息（非 loading、无思考无工具）→ 不渲染", () => {
    const { container } = render(<ThinkingTimeline message={message({})} loading={false} />);
    expect(container.querySelector(".yan-thought-timeline")).toBeNull();
  });
});
