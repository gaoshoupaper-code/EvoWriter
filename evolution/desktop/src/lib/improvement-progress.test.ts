import { expect, it } from "vitest";
import { improvementActivity } from "./improvement-progress";
import type { TraceNode } from "./types";

const tool = (tool_name: string, status: TraceNode["status"] = "completed") => ({ kind: "tool", status, tool_name });

it.each([
  ["read_improvement_sample", "核对本轮依据"],
  ["record_failure_review", "记录复盘决策"],
  ["write_design_doc", "拟定改动方案"],
  ["edit_source", "修改文件"],
  ["validate_changes", "校验候选"],
  ["write_change_log", "保存本轮结果"],
  ["finish_without_changes", "保存本轮结果"],
])("把工具 %s 映射为简短动作", (name, activity) => {
  expect(improvementActivity([tool(name)])).toBe(activity);
});

it("保留最后一个可识别工具，跳过其它节点和失败、取消的操作", () => {
  expect(improvementActivity([
    tool("read_assessment_round"),
    tool("write_design_doc"),
    tool("validate_changes", "running"),
    { ...tool("write_change_log"), kind: "llm" },
    tool("edit_source", "failed"),
    tool("finish_without_changes", "cancelled"),
    tool("unknown_command"),
  ])).toBe("校验候选");
});

it("没有可识别工具时留空", () => {
  expect(improvementActivity([])).toBe("");
  expect(improvementActivity([tool("unknown_command"), tool("write_design_doc", "failed")])).toBe("");
});
