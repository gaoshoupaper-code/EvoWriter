import type { TraceNode } from "./types";

const activities = new Map([
  ["list_assessment_history", "核对本轮依据"],
  ["search_assessment_history", "核对本轮依据"],
  ["read_assessment_round", "核对本轮依据"],
  ["read_improvement_sample", "核对本轮依据"],
  ["list_writing_events", "核对本轮依据"],
  ["read_writing_event", "核对本轮依据"],
  ["read_source", "核对本轮依据"],
  ["record_failure_review", "记录复盘决策"],
  ["write_design_doc", "拟定改动方案"],
  ["edit_source", "修改文件"],
  ["delete_file", "修改文件"],
  ["write_prompt", "修改文件"],
  ["write_middleware", "修改文件"],
  ["write_tool", "修改文件"],
  ["write_skill", "修改文件"],
  ["write_subagent", "修改文件"],
  ["validate_changes", "校验候选"],
  ["write_change_log", "保存本轮结果"],
  ["finish_without_changes", "保存本轮结果"],
]);

export function improvementActivity(nodes: Pick<TraceNode, "tool_name" | "kind" | "status">[]): string {
  for (let index = nodes.length - 1; index >= 0; index--) {
    const { kind, status, tool_name } = nodes[index];
    if (kind !== "tool" || (status !== "running" && status !== "completed") || !tool_name) continue;
    const activity = activities.get(tool_name);
    if (activity) return activity;
  }
  return "";
}
