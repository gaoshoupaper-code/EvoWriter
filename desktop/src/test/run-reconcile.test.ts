/**
 * 恢复对账纯函数测试（D-对账）。
 *
 * 场景矩阵：awaiting（重建拍板卡）/ 终态收敛 / running 不动 / idle 落 stopped /
 * 非陈旧尾部不动 / trace 不匹配保守不动 / awaiting 无详情宁缺毋滥。
 */
import { describe, it, expect } from "vitest";
import { reconcileMessagesWithRunState } from "@/lib/run-reconcile";
import type { ThreadRunState } from "@/lib/api";
import type { ChatMessage } from "@/lib/types";

const NOW = "2026-10-19T12:00:00.000Z";

function state(partial: Partial<ThreadRunState>): ThreadRunState {
  return { trace_id: "trace-1", status: "awaiting_input", awaiting: null, ...partial };
}

function messages(last: Partial<ChatMessage> = {}): ChatMessage[] {
  return [
    { role: "user", content: "写个故事" },
    { role: "assistant", content: "正在执行...", ...last },
  ];
}

describe("reconcileMessagesWithRunState", () => {
  it("awaiting_input 重建拍板卡：占位换问题文本 + 补 traceId + askedAt 用服务器时间", () => {
    const next = reconcileMessagesWithRunState(messages(), state({
      awaiting: {
        kind: "choice",
        question: "选个方向",
        options: [{ label: "A", description: "打脸" }],
        multi_select: false,
        source: "author-confirm",
        asked_at: "2026-10-18T20:17:24.99Z",
      },
    }), NOW);
    expect(next).not.toBeNull();
    const last = next![next!.length - 1];
    expect(last.status).toBeUndefined();
    expect(last.traceId).toBe("trace-1");
    expect(last.content).toBe("选个方向");
    expect(last.awaitingInput).toMatchObject({
      kind: "choice",
      question: "选个方向",
      askedAt: "2026-10-18T20:17:24.99Z",
      source: "author-confirm",
    });
    expect(last.awaitingInput?.options).toHaveLength(1);
  });

  it("awaiting_input 保留行模式真实正文（提问不覆盖内容）", () => {
    const next = reconcileMessagesWithRunState(messages({ content: "已生成的正文", rows: [] }), state({
      awaiting: { kind: "choice", question: "选个方向", options: null, multi_select: false },
    }), NOW);
    expect(next![next!.length - 1].content).toBe("已生成的正文");
  });

  it.each(["batch", "style", "stage"] as const)("恢复 %s 审查卡保留审查类型和独立编号", (reviewKind) => {
    const next = reconcileMessagesWithRunState(messages(), state({
      awaiting: {
        kind: "review", review_kind: reviewKind, review_id: "review-current",
        question: "本批已完成", options: null, multi_select: false, source: "review",
      },
    }), NOW);
    expect(next![next!.length - 1].awaitingInput).toMatchObject({
      kind: "review", review_kind: reviewKind, review_id: "review-current",
    });
  });

  it("awaiting 但服务端重建失败（无详情）→ 不动", () => {
    expect(reconcileMessagesWithRunState(messages(), state({ awaiting: null }), NOW)).toBeNull();
  });

  it("running → 不动（服务端真在跑，占位是诚实显示）", () => {
    expect(reconcileMessagesWithRunState(messages(), state({ status: "running" }), NOW)).toBeNull();
  });

  it("completed → 落终态 + 占位换说明；真实正文保留", () => {
    const next = reconcileMessagesWithRunState(messages(), state({ status: "completed" }), NOW);
    expect(next![next!.length - 1].status).toBe("completed");
    expect(next![next!.length - 1].content).toContain("已完成");

    const kept = reconcileMessagesWithRunState(messages({ content: "已生成的正文" }), state({ status: "completed" }), NOW);
    expect(kept![kept!.length - 1].content).toBe("已生成的正文");
  });

  it("failed / cancelled → 落对应终态", () => {
    const failed = reconcileMessagesWithRunState(messages(), state({ status: "failed" }), NOW);
    expect(failed![failed!.length - 1].status).toBe("failed");
    const cancelled = reconcileMessagesWithRunState(messages(), state({ status: "cancelled" }), NOW);
    expect(cancelled![cancelled!.length - 1].status).toBe("stopped");
  });

  it("idle（服务器无 run 记录）→ 落 stopped", () => {
    const next = reconcileMessagesWithRunState(messages(), state({ trace_id: null, status: "idle" }), NOW);
    expect(next![next!.length - 1].status).toBe("stopped");
  });

  it("尾部消息已有终态 / 已有卡点 → 不动", () => {
    expect(reconcileMessagesWithRunState(messages({ status: "completed" }), state({ status: "completed" }), NOW)).toBeNull();
    expect(reconcileMessagesWithRunState(messages({
      awaitingInput: { kind: "choice", question: "q" },
    }), state({ status: "awaiting_input" }), NOW)).toBeNull();
  });

  it("尾部不是 assistant（用户消息收尾）→ 不动", () => {
    const msgs: ChatMessage[] = [{ role: "user", content: "hi" }];
    expect(reconcileMessagesWithRunState(msgs, state({ status: "completed" }), NOW)).toBeNull();
  });

  it("消息 traceId 与服务端最新 run 不匹配（他端提交过新 run）→ 保守不动", () => {
    expect(reconcileMessagesWithRunState(messages({ traceId: "trace-old" }), state({ status: "awaiting_input" }), NOW)).toBeNull();
  });

  it("消息无 traceId（断在 run_start 前）也能对账", () => {
    const next = reconcileMessagesWithRunState(messages(), state({
      status: "awaiting_input",
      awaiting: { kind: "choice", question: "选个方向", options: null, multi_select: false },
    }), NOW);
    expect(next![next!.length - 1].traceId).toBe("trace-1");
  });
});
