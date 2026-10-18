/**
 * 行归并纯函数测试（REQ-20261018-235653 FR-001/FR-003/FR-006）。
 *
 * 行事件协议（DEC-003 行寻址 SSE）三种 op：
 * - open  {seq,id,kind[,response_id,label,text,tool,call_id,subagent_type,iteration]}
 * - delta {id,text}
 * - end   {id,state[,text,summary,duration_ms]}
 */
import { describe, expect, it } from "vitest";
import { applyRowEvent, closeStreamingRows, concatTextRows } from "@/lib/rows";
import type { TimelineRow } from "@/lib/types";

const T0 = 1_700_000_000_000;

function open(seq: number, id: string, kind: TimelineRow["kind"], extra: Record<string, unknown> = {}) {
  return applyRowEvent(undefined, { op: "open", seq, id, kind, ...extra }, T0);
}

describe("applyRowEvent · open", () => {
  it("开行：建 streaming 行并按 seq 插入", () => {
    let rows = open(2, "r2", "text");
    rows = applyRowEvent(rows, { op: "open", seq: 1, id: "r1", kind: "reasoning" }, T0 + 1);
    expect(rows.map((r) => r.id)).toEqual(["r1", "r2"]);
    expect(rows[0].status).toBe("streaming");
    expect(rows[0].startedAt).toBe(T0 + 1);
  });

  it("开行带全量文本（非流式调用，FR-002）：text 直接入行", () => {
    const rows = open(1, "r1", "text", { text: "确认卡全文" });
    expect(rows[0].text).toBe("确认卡全文");
  });

  it("畸形 open 丢弃：未知 kind / 缺 id / 重复 id / 非法 seq（FR-003 失败语义）", () => {
    let rows = open(1, "r1", "text");
    rows = applyRowEvent(rows, { op: "open", seq: 2, id: "rx", kind: "bogus" }, T0);
    rows = applyRowEvent(rows, { op: "open", seq: 3, kind: "text" }, T0);
    rows = applyRowEvent(rows, { op: "open", seq: 4, id: "r1", kind: "text" }, T0);
    rows = applyRowEvent(rows, { op: "open", seq: "x", id: "r9", kind: "text" }, T0);
    expect(rows.map((r) => r.id)).toEqual(["r1"]);
  });

  it("open 携带工具/子代理元信息", () => {
    let rows = open(1, "t1", "tool", { tool: "write_file", call_id: "c1" });
    rows = applyRowEvent(rows, { op: "open", seq: 2, id: "s1", kind: "subagent", subagent_type: "storybuilding", iteration: 3 }, T0);
    expect(rows[0].toolName).toBe("write_file");
    expect(rows[1].subagentType).toBe("storybuilding");
    expect(rows[1].iteration).toBe(3);
  });
});

describe("applyRowEvent · delta / end", () => {
  it("增量追加到对应行；找不到行或不接受增量的 kind 时忽略", () => {
    let rows = open(1, "r1", "text");
    rows = applyRowEvent(rows, { op: "delta", id: "r1", text: "你好" }, T0 + 1);
    rows = applyRowEvent(rows, { op: "delta", id: "r1", text: "，世界" }, T0 + 2);
    rows = applyRowEvent(rows, { op: "delta", id: "nope", text: "孤儿" }, T0 + 3);
    let tools = open(1, "t1", "tool");
    tools = applyRowEvent(tools, { op: "delta", id: "t1", text: "工具不吃文本" }, T0);
    expect(rows[0].text).toBe("你好，世界");
    expect(tools[0].text).toBeUndefined();
  });

  it("end 携带全量 text 时替换行文本（流式/全量对齐去重，AC-002 边界）", () => {
    let rows = open(1, "r1", "text");
    rows = applyRowEvent(rows, { op: "delta", id: "r1", text: "流式片段" }, T0);
    rows = applyRowEvent(rows, { op: "end", id: "r1", state: "complete", text: "完整正文" }, T0 + 5);
    expect(rows[0].text).toBe("完整正文");
    expect(rows[0].status).toBe("complete");
    expect(rows[0].endedAt).toBe(T0 + 5);
  });

  it("end 后的迟到 delta 丢弃；非法 state 忽略；summary 入子代理行", () => {
    let rows = open(1, "r1", "text");
    rows = applyRowEvent(rows, { op: "end", id: "r1", state: "complete", text: "done" }, T0);
    rows = applyRowEvent(rows, { op: "delta", id: "r1", text: "迟到" }, T0 + 1);
    expect(rows[0].text).toBe("done");
    rows = applyRowEvent(rows, { op: "end", id: "r1", state: "weird" }, T0 + 2);
    expect(rows[0].status).toBe("complete");

    let subs = open(1, "s1", "subagent", { subagent_type: "storybuilding" });
    subs = applyRowEvent(subs, { op: "end", id: "s1", state: "complete", summary: "第 1 轮完成" }, T0);
    expect(subs[0].summary).toBe("第 1 轮完成");
  });
});

describe("closeStreamingRows / concatTextRows", () => {
  it("中断关行：仅 streaming 行置 interrupted，终态行不动；无 streaming 行返回原引用", () => {
    let rows = open(1, "r1", "reasoning");
    rows = applyRowEvent(rows, { op: "end", id: "r1", state: "complete" }, T0);
    rows = open(2, "r2", "text") ? applyRowEvent(rows, { op: "open", seq: 2, id: "r2", kind: "text" }, T0) : rows;
    const closed = closeStreamingRows(rows, "interrupted", T0 + 9);
    expect(closed[0].status).toBe("complete");
    expect(closed[1].status).toBe("interrupted");
    expect(closed[1].endedAt).toBe(T0 + 9);
    const again = closeStreamingRows(closed, "interrupted", T0 + 10);
    expect(again).toBe(closed);
  });

  it("正文行拼接：按序取有文本的正文行，跳过空行", () => {
    let rows = open(1, "r1", "text", { text: "第一段" });
    rows = applyRowEvent(rows, { op: "open", seq: 2, id: "t2", kind: "tool" }, T0);
    rows = applyRowEvent(rows, { op: "open", seq: 3, id: "r3", kind: "text", text: "" }, T0);
    rows = applyRowEvent(rows, { op: "open", seq: 4, id: "r4", kind: "text", text: "第二段" }, T0);
    expect(concatTextRows(rows)).toBe("第一段\n\n第二段");
  });
});
