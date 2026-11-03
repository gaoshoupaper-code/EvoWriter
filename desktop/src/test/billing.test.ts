import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ fetchMyBilling: vi.fn() }));
vi.mock("@/lib/api", () => api);

const { useBillingStore, budgetError } = await import("@/stores/billing");

const account = () => ({
  enabled: true, protocol: 2, contact_qq: "1134559830",
  balance: 50, total_balance: 70, reserved: 20,
  default_budgets: { default: 10, text: 20 }, operations: [], transactions: [],
});

beforeEach(() => {
  vi.clearAllMocks();
  useBillingStore.getState().reset();
  useBillingStore.getState().setOwner("alice");
});

describe("积分账户与预算", () => {
  it("优先采用阶段预算，手动修改不会被余额刷新覆盖", async () => {
    api.fetchMyBilling.mockResolvedValue(account());
    await useBillingStore.getState().refresh();
    useBillingStore.getState().selectPhase("text");
    expect(useBillingStore.getState().budget).toBe("20");
    useBillingStore.getState().setBudget("12.34");
    await useBillingStore.getState().refresh();
    expect(await useBillingStore.getState().submissionBudget("text")).toBe(12.34);
  });

  it("查询失败保留旧值但标记未更新，不生成零余额", async () => {
    api.fetchMyBilling.mockResolvedValueOnce(account()).mockRejectedValue(new Error("offline"));
    await useBillingStore.getState().refresh();
    expect(await useBillingStore.getState().refresh()).toBeNull();
    expect(useBillingStore.getState().account?.balance).toBe(50);
    expect(useBillingStore.getState().error).toBe("offline");
    await expect(useBillingStore.getState().submissionBudget("text")).rejects.toThrow("offline");
  });

  it("刷新期间默认预算上涨，不改变用户点击时看到的上限", async () => {
    api.fetchMyBilling.mockResolvedValueOnce(account()).mockResolvedValue({ ...account(), default_budgets: { default: 40 } });
    await useBillingStore.getState().refresh();
    expect(useBillingStore.getState().budget).toBe("10");
    expect(await useBillingStore.getState().submissionBudget("default")).toBe(10);
  });

  it("切换账号后丢弃上一账号迟到的账户响应", async () => {
    let release!: (value: ReturnType<typeof account>) => void;
    api.fetchMyBilling.mockReturnValueOnce(new Promise((resolve) => { release = resolve; }));
    const pending = useBillingStore.getState().refresh();
    useBillingStore.getState().setOwner("bob");
    release(account());
    await pending;
    expect(useBillingStore.getState().account).toBeNull();
    expect(useBillingStore.getState().budget).toBe("");
  });

  it("拒绝零预算、超精度、无穷数和超过可用余额的预算", () => {
    for (const value of ["", "0", "-1", "0.001", "Infinity", "51"]) {
      expect(budgetError(value, 50)).toBeTruthy();
    }
    expect(budgetError("50.00", 50)).toBe("");
    expect(budgetError("0.01", 50)).toBe("");
  });
});
