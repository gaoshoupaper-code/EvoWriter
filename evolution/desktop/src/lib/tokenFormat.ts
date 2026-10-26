/**
 * token 万分位格式化（REQ-20261027-012631 FR-005 / DEC-012）。
 *
 * 三层用量观测共用的唯一格式化函数：一律以万（w）为单位保留 1 位小数；
 * 无数据显示「—」不得显示 0——历史 trace 节点缺 usage 与真实 0 消耗是两回事。
 */

export function formatTokensW(tokens: number | null | undefined): string {
  if (tokens == null) return "—";
  if (tokens === 0) return "0w";
  return `${(tokens / 10000).toFixed(1)}w`;
}
