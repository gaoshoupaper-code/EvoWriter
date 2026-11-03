export function parseCreditAmount(value: string): number {
  if (!/^[+-]?\d+(?:\.\d{1,2})?$/.test(value.trim())) return NaN;
  const amount = Number(value);
  return Number.isFinite(amount) && Math.abs(amount * 100) <= 9_000_000_000_000_000 ? amount : NaN;
}

export function formatCredits(value: number | string | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "未返回";
  return Number(value).toFixed(2);
}

export const BILLING_STATUS: Record<string, string> = {
  reserved: "已预留", running: "执行中", processing: "结算处理中",
  completed: "已完成", stopped: "主动停止", budget_exhausted: "预算用尽", failed: "故障退分",
};
