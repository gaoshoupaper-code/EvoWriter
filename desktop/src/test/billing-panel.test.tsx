import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const api = vi.hoisted(() => ({ fetchMyBilling: vi.fn(), fetchBillingOperation: vi.fn() }));
vi.mock("@/lib/api", () => api);
import { BillingPanel } from "@/components/billing/BillingPanel";
import { useBillingStore } from "@/stores/billing";

const operation = { operation_id: "operation", user_id: "owner", thread_id: "thread", request_id: "request", budget: 20,
  status: "completed", consumed: 2.34, charged: 2.34, missing_usage: 1, trace_id: "trace", created_at: "2026-11-03T00:00:00Z", settled_at: "2026-11-03T00:01:00Z" };

beforeEach(() => {
  vi.clearAllMocks();
  useBillingStore.getState().reset();
  useBillingStore.getState().setOwner("owner");
  api.fetchMyBilling.mockResolvedValue({ enabled: true, protocol: 2, contact_qq: "1134559830", balance: 50, total_balance: 70,
    reserved: 20, default_budgets: { default: 10 }, operations: [operation], transactions: [] });
});

describe("用户积分账单", () => {
  it("显示余额和实际用量，兼容旧响应时也不暴露采购成本或换算", async () => {
    api.fetchBillingOperation.mockResolvedValue({ ...operation, prices: { model: { input_per_million: "983.000" } },
      result: { content: "已保存的正文" }, calls: [
        { model: "model", input_tokens: 100, output_tokens: 20, cached_tokens: 50, usage_known: 1, billable: 0, cost: "923.4567", platform_cost: "923.4567" },
        { model: "model", input_tokens: 100, output_tokens: null, cached_tokens: 0, usage_known: 0, billable: 1, cost: "0", platform_cost: null },
      ] });
    const { container } = render(<MemoryRouter><BillingPanel /></MemoryRouter>);
    await screen.findByText("50.00");
    const row = container.querySelector("details.billing-operation") as HTMLDetailsElement;
    row.open = true;
    fireEvent(row, new Event("toggle"));
    await screen.findByText("故障重试，平台承担");
    expect(screen.getByText("用量缺失，不估扣")).toBeInTheDocument();
    expect(screen.getByText("实扣 2.34 分")).toBeInTheDocument();
    expect(screen.getByText("查看已保存结果")).toBeInTheDocument();
    expect(screen.queryByText(/采购|模型成本|人民币|983\.000|923\.4567|百万 Token|1 元/)).not.toBeInTheDocument();
    expect(screen.getByText("1134559830")).toBeInTheDocument();
  });

  it("账户查询失败明确提示，不生成虚假的零余额", async () => {
    api.fetchMyBilling.mockRejectedValue(new Error("服务暂不可用"));
    render(<MemoryRouter><BillingPanel /></MemoryRouter>);
    expect(await screen.findByRole("alert")).toHaveTextContent("服务暂不可用");
    expect(screen.queryByText("0.00")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新账目" })).toBeInTheDocument();
  });
});
