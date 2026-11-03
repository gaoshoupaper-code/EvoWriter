import { create } from "zustand";
import { fetchMyBilling, type BillingAccount, type BillingOperation } from "@/lib/api";

let generation = 0;
let pending: { generation: number; promise: Promise<BillingAccount | null> } | null = null;

export function budgetError(value: string, available: number): string {
  if (!/^\d+(?:\.\d{1,2})?$/.test(value.trim())) return "预算最多保留两位小数";
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return "本次预算必须大于零";
  if (number > available) return "预算超过可用积分，请降低预算或联系管理员补分";
  return "";
}

export const billingStatus: Record<string, string> = {
  reserved: "已预留", running: "执行中", processing: "结算处理中",
  completed: "已完成", stopped: "主动停止", budget_exhausted: "预算用尽", failed: "系统故障，积分退还",
};

type BillingState = {
  owner: string;
  account: BillingAccount | null;
  loading: boolean;
  error: string;
  phase: string;
  budget: string;
  budgetEdited: boolean;
  setOwner: (owner: string) => void;
  reset: () => void;
  refresh: () => Promise<BillingAccount | null>;
  selectPhase: (phase: string) => void;
  setBudget: (value: string) => void;
  submissionBudget: (phase: string) => Promise<number | undefined>;
  receiveOperation: (operation: BillingOperation) => void;
};

const defaults = () => ({ owner: "", account: null, loading: false, error: "", phase: "default", budget: "", budgetEdited: false });

function defaultBudget(account: BillingAccount | null, phase: string): string {
  const value = account?.default_budgets[phase] ?? account?.default_budgets.default;
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? String(value) : "";
}

export const useBillingStore = create<BillingState>((set, get) => ({
  ...defaults(),
  reset: () => {
    generation += 1;
    pending = null;
    set(defaults());
  },
  setOwner: (owner) => {
    if (get().owner === owner) return;
    get().reset();
    set({ owner });
  },
  refresh: () => {
    if (pending?.generation === generation) return pending.promise;
    const started = generation;
    set({ loading: true });
    const promise = (async () => {
      try {
        const account = await fetchMyBilling();
        if (started !== generation) return null;
        if (!account || typeof account.balance !== "number" || !account.default_budgets) {
          throw new Error("无法获取积分账户，请重试");
        }
        set((state) => ({
          account, error: "", loading: false,
          ...(!state.budgetEdited ? { budget: defaultBudget(account, state.phase) } : {}),
        }));
        return account;
      } catch (error) {
        if (started === generation) {
          set({ error: error instanceof Error ? error.message : "无法获取积分账户，请重试", loading: false });
        }
        return null;
      } finally {
        if (pending?.generation === started) pending = null;
      }
    })();
    pending = { generation: started, promise };
    return promise;
  },
  selectPhase: (phase) => {
    if (get().phase === phase) return;
    set({ phase, budget: defaultBudget(get().account, phase), budgetEdited: false });
  },
  setBudget: (budget) => set({ budget, budgetEdited: true }),
  submissionBudget: async (phase) => {
    get().selectPhase(phase);
    const visibleBudget = get().budget;
    const wasEnabled = get().account?.enabled;
    const started = generation;
    const account = await get().refresh();
    if (started !== generation) throw new Error("账号已切换，请重新确认预算");
    if (!account) throw new Error(get().error || "无法获取积分账户，请重试");
    if (!account.enabled) return undefined;
    if (wasEnabled !== true) throw new Error("积分计费已启用，请先确认本次预算后再发送");
    const error = budgetError(visibleBudget, account.balance);
    if (error) throw new Error(error);
    return Number(visibleBudget);
  },
  receiveOperation: (operation) => {
    set((state) => state.account ? {
      account: { ...state.account, operations: [operation, ...state.account.operations.filter((row) => row.operation_id !== operation.operation_id)].slice(0, 50) },
    } : {});
    void get().refresh();
  },
}));
