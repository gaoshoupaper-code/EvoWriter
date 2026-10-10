/**
 * thoughts —— 思考流分段纯函数（FR-003/FR-005，REQ-20261010-182114）
 *
 * 分段规则（DEC-005）：reasoning token 连续到达视为同一段；
 * tool 事件（tool_call/tool_output/tool_error）是段边界——关闭当前段，
 * 下一个 reasoning token 开新段。中断（停止/失败/提问）把 streaming 段
 * 标记为 interrupted（DEC-007），正常完成标 done。
 *
 * 纯函数 + 不可变更新：store 在 SSE 帧批内累积、按 reader chunk 批量
 * flush 进 message.thoughts（FR-007 性能：与 streamedText 同一节拍）。
 */
import type { ThoughtSegment, ToolStatus } from "./types";
import { TOOL_DISPLAY_NAMES } from "./yan-copy";

/** 推断当前活跃子任务的人话名：最近的 running 工具优先 subagent 信号，回退工具名。 */
export function currentTaskLabel(tools: ToolStatus[] | undefined): string | undefined {
  const running = (tools ?? []).findLast((t) => t.status === "running");
  const source = running?.subagentType ?? running?.subagentName ?? running?.name;
  if (!source) return undefined;
  return TOOL_DISPLAY_NAMES[source] ?? source;
}

/** reasoning token 追加：末段 streaming 则续写，否则开新段（label 为开段时快照）。 */
export function appendReasoning(
  segments: ThoughtSegment[],
  content: string,
  label: string | undefined,
  now: number = Date.now(),
): ThoughtSegment[] {
  if (!content) return segments;
  const last = segments[segments.length - 1];
  if (last && last.status === "streaming") {
    const next = [...segments];
    next[next.length - 1] = { ...last, text: last.text + content };
    return next;
  }
  const id = last ? last.id + 1 : 1;
  return [...segments, { id, text: content, label, startedAt: now, status: "streaming" }];
}

/**
 * 关闭末尾 streaming 段：tool 边界 → done，中断 → interrupted。
 * 没有 streaming 段时原样返回同一引用（调用方据此免写 message）。
 */
export function closeStreamingThought(
  segments: ThoughtSegment[],
  status: "done" | "interrupted",
  now: number = Date.now(),
): ThoughtSegment[] {
  const last = segments[segments.length - 1];
  if (!last || last.status !== "streaming") return segments;
  const next = [...segments];
  next[next.length - 1] = { ...last, status, endedAt: now };
  return next;
}
