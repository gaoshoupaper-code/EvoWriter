/**
 * 批次状态展示（评测工作台 / 产物库共用，避免两份同名实现漂移）。
 */

export function batchStatusLabel(s: string): string {
  const map: Record<string, string> = {
    running: "运行中", done: "完成", partial: "部分完成", failed: "失败",
    cancelled: "已停止",
  };
  return map[s] ?? s;
}

export function batchStatusClass(s: string): string {
  // 复用 session-status 的语义色（done/failed 现成；running/partial/cancelled 就近映射）
  if (s === "done") return "done";
  if (s === "failed") return "failed";
  if (s === "running") return "running";
  return "pending";
}
