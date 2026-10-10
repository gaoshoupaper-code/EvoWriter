/**
 * 思考流分段纯函数（lib/thoughts.ts）单元测试
 * FR-003/FR-005（REQ-20261010-182114）：
 * - reasoning 连续到达同段续写；tool 边界关段、下个 reasoning 开新段
 * - 中断标 interrupted；无 streaming 段时关段为幂等 no-op
 * - label 开段时快照；currentTaskLabel 从 tools 推断人话名
 */
import { describe, expect, it } from "vitest";
import { appendReasoning, closeStreamingThought, currentTaskLabel } from "@/lib/thoughts";
import type { ThoughtSegment, ToolStatus } from "@/lib/types";

describe("appendReasoning（FR-003 分段收集）", () => {
  it("连续 token 续写同一段", () => {
    const t0 = 1000;
    let segs = appendReasoning([], "The user", undefined, t0);
    segs = appendReasoning(segs, " wants", undefined, t0 + 5);
    segs = appendReasoning(segs, " a story", undefined, t0 + 10);

    expect(segs).toHaveLength(1);
    expect(segs[0].text).toBe("The user wants a story");
    expect(segs[0].status).toBe("streaming");
    expect(segs[0].startedAt).toBe(t0);
  });

  it("tool 关段后的 reasoning 开新段，id 递增，label 为开段时快照", () => {
    let segs = appendReasoning([], "first", "构思故事", 1000);
    segs = closeStreamingThought(segs, "done", 2000);
    segs = appendReasoning(segs, "second", "正文写作", 3000);

    expect(segs).toHaveLength(2);
    expect(segs[0]).toMatchObject({ id: 1, status: "done", label: "构思故事", endedAt: 2000 });
    expect(segs[1]).toMatchObject({ id: 2, status: "streaming", label: "正文写作", startedAt: 3000 });
  });

  it("空字符串 token 不产生段也不改引用", () => {
    const segs: ThoughtSegment[] = [];
    expect(appendReasoning(segs, "", "x", 1)).toBe(segs);
  });

  it("不可变更新：原数组不被修改", () => {
    const first = appendReasoning([], "a", undefined, 1);
    const snapshot = [...first];
    appendReasoning(first, "b", undefined, 2);
    expect(first).toEqual(snapshot);
  });
});

describe("closeStreamingThought（FR-005 终态）", () => {
  it("tool 边界 → done，记录 endedAt", () => {
    let segs = appendReasoning([], "thinking...", undefined, 1000);
    segs = closeStreamingThought(segs, "done", 5000);
    expect(segs[0]).toMatchObject({ status: "done", endedAt: 5000 });
  });

  it("中断 → interrupted（DEC-007）", () => {
    let segs = appendReasoning([], "half", undefined, 1000);
    segs = closeStreamingThought(segs, "interrupted", 3000);
    expect(segs[0].status).toBe("interrupted");
  });

  it("无 streaming 段时幂等 no-op，返回同一引用", () => {
    let segs = appendReasoning([], "x", undefined, 1);
    segs = closeStreamingThought(segs, "done", 2);
    const again = closeStreamingThought(segs, "done", 3);
    expect(again).toBe(segs);

    const empty = closeStreamingThought([], "done", 1);
    expect(empty).toEqual([]);
  });
});

describe("currentTaskLabel（FR-003 子任务名推断）", () => {
  const tool = (over: Partial<ToolStatus>): ToolStatus =>
    ({ key: "k", name: "task", status: "running", ...over }) as ToolStatus;

  it("最近 running 工具的 subagentType → 人话名", () => {
    expect(currentTaskLabel([tool({ subagentType: "storybuilding" })])).toBe("构思故事");
  });

  it("未知 subagent 名原样返回", () => {
    expect(currentTaskLabel([tool({ subagentType: "unknown-agent" })])).toBe("unknown-agent");
  });

  it("无 running 工具 → undefined", () => {
    expect(currentTaskLabel([{ key: "k", name: "task", status: "done" }])).toBeUndefined();
    expect(currentTaskLabel(undefined)).toBeUndefined();
  });
});
