/**
 * rows —— 行事件归并纯函数（REQ-20261018-235653，FR-001/FR-003/FR-006）。
 *
 * 行事件协议（DEC-003 行寻址 SSE，单事件类型 `row` 三种 op）：
 * - open  {op, seq, id, kind[, response_id, label, text, tool, call_id, subagent_type, iteration]}
 * - delta {op, id, text}
 * - end   {op, id, state[, text, summary]}
 *
 * 与 thoughts.ts 同一设计约束：纯函数 + 不可变更新，store 在 SSE 帧批内
 * 累积、按 reader chunk 批量 flush 进 message.rows（随消息持久化，FR-006）。
 * 畸形载荷（未知 kind / 缺 id / 非法 seq / 非法 state）一律丢弃该事件，
 * 不影响其余行（FR-003 失败语义）。
 */
import type { TimelineRow, TimelineRowKind, TimelineRowStatus } from "./types";

const ROW_KINDS: readonly TimelineRowKind[] = ["reasoning", "text", "tool", "subagent"];
const TERMINAL_STATES: readonly string[] = ["complete", "failed"];

/** 应用一条行事件；无有效变更时返回原引用（调用方据此免写 message）。 */
export function applyRowEvent(
  rows: TimelineRow[] | undefined,
  data: Record<string, unknown>,
  now: number = Date.now(),
): TimelineRow[] {
  const op = data["op"];

  if (op === "open") return applyOpen(rows, data, now);
  if (op === "delta") return applyDelta(rows, data);
  if (op === "end") return applyEnd(rows, data, now);
  return rows ?? [];
}

function applyOpen(rows: TimelineRow[] | undefined, data: Record<string, unknown>, now: number): TimelineRow[] {
  const seq = data["seq"];
  const id = data["id"];
  const kind = data["kind"];
  if (typeof seq !== "number" || !Number.isFinite(seq)) return rows ?? [];
  if (typeof id !== "string" || !id) return rows ?? [];
  if (typeof kind !== "string" || !ROW_KINDS.includes(kind as TimelineRowKind)) return rows ?? [];

  const current = rows ?? [];
  // 幂等：同 id 重复 open（重放/网络重发）不重复建行
  if (current.some((row) => row.id === id)) return current;

  const text = typeof data["text"] === "string" ? (data["text"] as string) : undefined;
  const row: TimelineRow = {
    seq,
    id,
    kind: kind as TimelineRowKind,
    status: "streaming",
    startedAt: now,
    ...(typeof data["response_id"] === "string" ? { responseId: data["response_id"] } : {}),
    ...(typeof data["label"] === "string" ? { label: data["label"] } : {}),
    ...(text !== undefined ? { text } : {}),
    ...(typeof data["tool"] === "string" ? { toolName: data["tool"] } : {}),
    ...(typeof data["call_id"] === "string" ? { callId: data["call_id"] } : {}),
    ...(typeof data["subagent_type"] === "string" ? { subagentType: data["subagent_type"] } : {}),
    ...(typeof data["iteration"] === "number" ? { iteration: data["iteration"] } : {}),
  };

  // 按 seq 保序插入（服务端序即时间线序，FR-001）
  const next = [...current];
  let insertAt = next.length;
  while (insertAt > 0 && next[insertAt - 1].seq > seq) insertAt -= 1;
  next.splice(insertAt, 0, row);
  return next;
}

function applyDelta(rows: TimelineRow[] | undefined, data: Record<string, unknown>): TimelineRow[] {
  const current = rows ?? [];
  const id = data["id"];
  const text = data["text"];
  if (typeof id !== "string" || typeof text !== "string") return current;

  const index = current.findIndex((row) => row.id === id);
  if (index < 0) return current;
  const row = current[index];
  // 只有 streaming 的思考/正文行接受追加；工具/子代理行不吃文本，终态行丢弃迟到增量
  if (row.status !== "streaming" || (row.kind !== "text" && row.kind !== "reasoning")) return current;

  const next = [...current];
  next[index] = { ...row, text: (row.text ?? "") + text };
  return next;
}

function applyEnd(rows: TimelineRow[] | undefined, data: Record<string, unknown>, now: number): TimelineRow[] {
  const current = rows ?? [];
  const id = data["id"];
  const state = data["state"];
  if (typeof id !== "string" || typeof state !== "string" || !TERMINAL_STATES.includes(state)) return current;

  const index = current.findIndex((row) => row.id === id);
  if (index < 0) return current;
  const row = current[index];
  if (row.status !== "streaming") return current;

  const next = [...current];
  next[index] = {
    ...row,
    status: state as TimelineRowStatus,
    endedAt: now,
    // end 携带全量 text 时整体替换（流式增量与 on_chat_model_end 全量对齐去重，AC-002）
    ...(typeof data["text"] === "string" ? { text: data["text"] as string } : {}),
    ...(typeof data["summary"] === "string" ? { summary: data["summary"] as string } : {}),
  };
  return next;
}

/** 关闭末批 streaming 行（中断/停止/失败/断流时，FR-004）；无 streaming 行返回原引用。 */
export function closeStreamingRows(
  rows: TimelineRow[] | undefined,
  status: "interrupted" | "complete" | "failed",
  now: number = Date.now(),
): TimelineRow[] {
  const current = rows ?? [];
  if (!current.some((row) => row.status === "streaming")) return current;
  return current.map((row) => (row.status === "streaming" ? { ...row, status, endedAt: now } : row));
}

/** 正文行拼接（行模式下派生 message.content）：按序取有文本的正文行。 */
export function concatTextRows(rows: TimelineRow[] | undefined): string {
  return (rows ?? [])
    .filter((row) => row.kind === "text" && typeof row.text === "string" && row.text !== "")
    .map((row) => row.text as string)
    .join("\n\n");
}

/** 拍板已答定格：提问追加为正文行（时间线与派生正文都可回看，不随卡消失）。 */
export function appendQuestionRow(rows: TimelineRow[], question: string, now: number = Date.now()): TimelineRow[] {
  const seq = rows.reduce((max, row) => Math.max(max, row.seq), 0) + 1;
  return [
    ...rows,
    {
      seq,
      // 本地生成的行，id 带前缀防与服务端行 id 撞车（幂等去重按 id 匹配）
      id: `answered-q-${seq}`,
      kind: "text",
      text: question,
      status: "complete",
      startedAt: now,
      endedAt: now,
    },
  ];
}
