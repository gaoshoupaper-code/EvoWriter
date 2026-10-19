/**
 * rows 纯函数单测（REQ-20261019-183921 FR-003/FR-004，移植自写作端
 * desktop/src/lib/rows.test.ts 场景，去 subagent）。
 */
import { describe, expect, it } from "vitest";
import { applyRowEvent, closeStreamingRows, type TimelineRow } from "./rows";

const T0 = 1_700_000_000_000;

function openRow(seq: number, id: string, kind: string, extra: Record<string, unknown> = {}) {
  return { type: "row", op: "open", seq, id, kind, ...extra };
}

describe("applyRowEvent.open", () => {
  it("建行并按 seq 保序插入", () => {
    let rows: TimelineRow[] | undefined;
    rows = applyRowEvent(rows, openRow(2, "r2", "text"), T0);
    rows = applyRowEvent(rows, openRow(1, "r1", "reasoning"), T0);
    expect(rows.map((r) => r.id)).toEqual(["r1", "r2"]);
    expect(rows[0].status).toBe("streaming");
    expect(rows[0].startedAt).toBe(T0);
  });

  it("同 id 幂等（不重复建行）", () => {
    let rows = applyRowEvent(undefined, openRow(1, "r1", "text"), T0);
    rows = applyRowEvent(rows, openRow(1, "r1", "text"), T0);
    expect(rows).toHaveLength(1);
  });

  it("畸形载荷丢弃：未知 kind / 缺 id / 非法 seq", () => {
    expect(applyRowEvent(undefined, openRow(1, "r1", "subagent"), T0)).toEqual([]);
    expect(applyRowEvent(undefined, openRow(1, "", "text"), T0)).toEqual([]);
    expect(applyRowEvent(undefined, { ...openRow(1, "r1", "text"), seq: "x" }, T0)).toEqual([]);
  });

  it("tool 字段映射到 toolName（snake_case 载荷）", () => {
    const rows = applyRowEvent(undefined, openRow(1, "t1", "tool", { tool: "list_elements", call_id: "c1" }), T0);
    expect(rows[0].toolName).toBe("list_elements");
    expect(rows[0].callId).toBe("c1");
  });
});

describe("applyRowEvent.delta", () => {
  it("streaming 思考/正文行追加文本", () => {
    let rows = applyRowEvent(undefined, openRow(1, "r1", "text"), T0);
    rows = applyRowEvent(rows, { type: "row", op: "delta", id: "r1", text: "你" }, T0);
    rows = applyRowEvent(rows, { type: "row", op: "delta", id: "r1", text: "好" }, T0);
    expect(rows[0].text).toBe("你好");
  });

  it("工具行不吃文本；终态行丢弃迟到增量", () => {
    let rows = applyRowEvent(undefined, openRow(1, "t1", "tool"), T0);
    rows = applyRowEvent(rows, { type: "row", op: "delta", id: "t1", text: "x" }, T0);
    expect(rows[0].text).toBeUndefined();

    let text = applyRowEvent(undefined, openRow(1, "r1", "text"), T0);
    text = applyRowEvent(text, { type: "row", op: "end", id: "r1", state: "complete" }, T0);
    text = applyRowEvent(text, { type: "row", op: "delta", id: "r1", text: "late" }, T0);
    expect(text[0].text).toBeUndefined();
  });
});

describe("applyRowEvent.end", () => {
  it("全量 text 整体替换 + 终态收尾", () => {
    let rows = applyRowEvent(undefined, openRow(1, "r1", "text"), T0);
    rows = applyRowEvent(rows, { type: "row", op: "delta", id: "r1", text: "部分" }, T0);
    rows = applyRowEvent(rows, { type: "row", op: "end", id: "r1", state: "complete", text: "全量" }, T0);
    expect(rows[0].status).toBe("complete");
    expect(rows[0].text).toBe("全量");
    expect(rows[0].endedAt).toBe(T0);
  });

  it("非法 state 丢弃", () => {
    let rows = applyRowEvent(undefined, openRow(1, "r1", "text"), T0);
    rows = applyRowEvent(rows, { type: "row", op: "end", id: "r1", state: "weird" }, T0);
    expect(rows[0].status).toBe("streaming");
  });
});

describe("closeStreamingRows", () => {
  it("批量关行（断流兜底）", () => {
    let rows = applyRowEvent(undefined, openRow(1, "r1", "reasoning"), T0);
    rows = applyRowEvent(rows, openRow(2, "r2", "text"), T0);
    rows = applyRowEvent(rows, { type: "row", op: "end", id: "r2", state: "complete" }, T0);
    const closed = closeStreamingRows(rows, "interrupted", T0 + 5);
    expect(closed[0].status).toBe("interrupted");
    expect(closed[1].status).toBe("complete"); // 已终态不动
  });

  it("无 streaming 行返回原引用", () => {
    const rows = applyRowEvent(undefined, openRow(1, "r1", "text"), T0);
    const ended = applyRowEvent(rows, { type: "row", op: "end", id: "r1", state: "complete" }, T0);
    expect(closeStreamingRows(ended, "failed", T0)).toBe(ended);
  });
});
