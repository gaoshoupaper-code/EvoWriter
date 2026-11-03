import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import AdminCredits from "./credits";
import AdminCreditsSettings from "./credits/settings";
import { fetchBillingConfig, fetchBillingOperations, fetchBillingOperation, fetchAllTransactions, fetchUserTransactions, fetchUsers, saveBillingConfig, saveBillingPrice } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  fetchBillingConfig: vi.fn(), saveBillingConfig: vi.fn(), saveBillingPrice: vi.fn(),
  fetchAllTransactions: vi.fn(), fetchBillingOperations: vi.fn(), fetchBillingOperation: vi.fn(),
  fetchUsers: vi.fn(), fetchUserTransactions: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

afterEach(cleanup);
const config = { enabled: true, default_budgets: { default: 20 }, contact_qq: "1134559830", protocol: 2,
  prices: [{ model: "model", input_per_million: "1", cached_per_million: "0", output_per_million: "2", updated_at: "2026-11-03T00:00:00Z" }] };
beforeEach(() => {
  vi.mocked(fetchBillingConfig).mockReset().mockResolvedValue(config);
  vi.mocked(saveBillingConfig).mockReset().mockResolvedValue(config);
  vi.mocked(saveBillingPrice).mockReset().mockResolvedValue(config.prices[0]);
  vi.mocked(fetchAllTransactions).mockReset().mockResolvedValue([]);
  vi.mocked(fetchBillingOperations).mockReset().mockResolvedValue([]);
  vi.mocked(fetchBillingOperation).mockReset();
  vi.mocked(fetchUserTransactions).mockReset().mockResolvedValue([]);
  vi.mocked(fetchUsers).mockReset().mockResolvedValue(["quiet", "another"].map((id) => ({
    user_id: id, username: id, credits_balance: 10, is_admin: false, is_super_admin: false,
    disabled: false, has_api_key: false, workspace_count: 0, created_at: config.prices[0].updated_at,
  })));
});

const renderCredits = (path = "/admin/credits") => render(<MemoryRouter initialEntries={[path]}><AdminCredits /></MemoryRouter>);
const transaction = (userId: string, note: string) => ({
  tx_id: userId, user_id: userId, type: "admin_adjust", amount: 1, balance_after: 10,
  ref_thread_id: null, ref_hold_id: null, note, created_by: "admin", created_at: config.prices[0].updated_at,
});

it("设置加载失败明确显示错误，不冒充已关闭计费或零预算", async () => {
  vi.mocked(fetchBillingConfig).mockRejectedValue(new Error("服务不可用"));
  render(<AdminCreditsSettings />);
  expect(await screen.findByRole("alert")).toHaveTextContent("服务不可用");
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(screen.queryByText("当前：计费未启用")).not.toBeInTheDocument();
});

it("预算保持两位小数，当前计费状态不会被保存成默认关闭", async () => {
  render(<AdminCreditsSettings />);
  const budget = await screen.findByLabelText("通用操作默认预算（积分）");
  expect(screen.getByRole("checkbox")).toBeChecked();
  fireEvent.change(budget, { target: { value: "12.34" } });
  fireEvent.submit(budget.closest("form")!);
  await waitFor(() => expect(saveBillingConfig).toHaveBeenCalledWith(true, { default: 12.34 }));
});

it("模型缺少系数不当成零提交，明确的零缓存系数可以保存", async () => {
  render(<AdminCreditsSettings />);
  const cached = await screen.findByLabelText("model · 缓存输入系数");
  fireEvent.change(cached, { target: { value: "" } });
  fireEvent.submit(cached.closest("form")!);
  expect(saveBillingPrice).not.toHaveBeenCalled();
  fireEvent.change(cached, { target: { value: "0" } });
  fireEvent.submit(cached.closest("form")!);
  await waitFor(() => expect(saveBillingPrice).toHaveBeenCalledWith("model", {
    input_per_million: "1", output_per_million: "2", cached_per_million: "0",
  }));
});

it("后台以积分系数配置，不展示人民币换算或公开采购成本", async () => {
  render(<AdminCreditsSettings />);
  expect(await screen.findByText("模型积分系数")).toBeInTheDocument();
  expect(screen.getByText(/单位：积分/)).toBeInTheDocument();
  expect(screen.queryByText(/人民币|100 积分|模型成本|公开价格/)).not.toBeInTheDocument();
});

it("未结算操作不显示成零消费，明细保留未知用量与免费重试", async () => {
  const operation = { operation_id: "op", user_id: "owner", thread_id: "thread", request_id: "request", budget: 20, status: "processing", consumed: 1, charged: 0, missing_usage: 1, trace_id: "trace", created_at: "2026-11-03T00:00:00Z", settled_at: null };
  vi.mocked(fetchBillingOperations).mockResolvedValue([operation]);
  vi.mocked(fetchBillingOperation).mockResolvedValue({ ...operation, prices: {}, result: null, calls: [
    { call_id: "retry", model: "model", input_tokens: 100, output_tokens: 10, cached_tokens: 0, cost: "0", platform_cost: "0.12", billable: 0, usage_known: 1, created_at: operation.created_at },
    { call_id: "unknown", model: "model", input_tokens: null, output_tokens: null, cached_tokens: null, cost: "0", platform_cost: null, billable: 1, usage_known: 0, created_at: operation.created_at },
  ] });
  renderCredits();
  expect(await screen.findByText("待结算")).toBeInTheDocument();
  expect(screen.queryByText("实际扣 0.00 分")).not.toBeInTheDocument();
  const summary = screen.getByText("待结算").closest("summary")!;
  await act(async () => {
    summary.parentElement!.setAttribute("open", "");
    fireEvent(summary.parentElement!, new Event("toggle"));
  });
  expect(await screen.findByText("故障重试，平台承担")).toBeInTheDocument();
  expect(screen.getByText("用量缺失，不估扣")).toBeInTheDocument();
  expect(screen.getAllByText("未返回")).toHaveLength(3);
});

it("直达用户账单时查询本人接口，切换全部用户时恢复全局查询", async () => {
  vi.mocked(fetchUserTransactions).mockResolvedValue([transaction("quiet", "低频用户历史发放")]);
  renderCredits("/admin/credits?user_id=quiet");
  expect(await screen.findByText("低频用户历史发放")).toBeInTheDocument();
  expect(fetchBillingOperations).toHaveBeenCalledWith(50, "quiet");
  expect(fetchUserTransactions).toHaveBeenCalledWith("quiet", 50);
  expect(fetchAllTransactions).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("账目条数"), { target: { value: "200" } });
  await waitFor(() => expect(fetchUserTransactions).toHaveBeenCalledWith("quiet", 200));
  expect(fetchBillingOperations).toHaveBeenCalledWith(200, "quiet");
  fireEvent.change(screen.getByLabelText("筛选用户"), { target: { value: "" } });
  await waitFor(() => expect(fetchAllTransactions).toHaveBeenCalledWith(200));
  expect(fetchBillingOperations).toHaveBeenCalledWith(200, "");
});

it("切换用户后丢弃上一用户迟到的流水响应", async () => {
  let finishQuiet!: (value: ReturnType<typeof transaction>[]) => void;
  vi.mocked(fetchUserTransactions).mockImplementation((userId) => userId === "quiet"
    ? new Promise((resolve) => { finishQuiet = resolve; })
    : Promise.resolve([transaction("another", "当前用户流水")]));
  renderCredits("/admin/credits?user_id=quiet");
  await waitFor(() => expect(fetchUserTransactions).toHaveBeenCalledWith("quiet", 50));
  fireEvent.change(screen.getByLabelText("筛选用户"), { target: { value: "another" } });
  expect(await screen.findByText("当前用户流水")).toBeInTheDocument();
  await act(async () => { finishQuiet([transaction("quiet", "迟到的旧用户流水")]); });
  expect(screen.queryByText("迟到的旧用户流水")).not.toBeInTheDocument();
  expect(screen.getByText("当前用户流水")).toBeInTheDocument();
});
