import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AutomaticTaskPanel } from "@/components/workspace/AutomaticTaskPanel";
import * as apiModule from "@/lib/api";
import { useBillingStore } from "@/stores/billing";

vi.mock("@/lib/api", () => ({
  listTargetTasks: vi.fn(), startTargetTask: vi.fn(),
  getTargetTask: vi.fn(), stopTargetTask: vi.fn(),
  fetchMyBilling: vi.fn(),
}));
vi.mock("@/lib/usePanelPolling", () => ({ refreshWorkspaceContent: vi.fn() }));

const api = vi.mocked(apiModule);
const account = (enabled = false, balance = 100) => ({
  enabled, balance, total_balance: balance, reserved: 0, protocol: 2, contact_qq: "1134559830",
  default_budgets: { default: 10, volume: 10, chapter: 20, text: 20 }, operations: [], transactions: [],
});
const task = (overrides: Record<string, unknown> = {}) => ({
  task_id: "target-1", status: "running", phase: "chapter", completed: false,
  trace_ids: ["trace-1"], artifacts: {}, checks: {}, usage: {},
  input_revision: "input-1", source_commit: "abc1234", attempts: 1,
  created_at: "2026-11-06T01:00:00Z", updated_at: "2026-11-06T01:00:00Z",
  ...overrides,
});

beforeEach(() => {
  vi.clearAllMocks();
  useBillingStore.getState().reset();
  useBillingStore.getState().setOwner("owner-1");
  useBillingStore.setState({ account: account(), budget: "10" });
  api.fetchMyBilling.mockResolvedValue(account());
  api.listTargetTasks.mockResolvedValue({ tasks: [] });
});

describe("真实作品全自动目标入口 / AC-009", () => {
  it("四卷任务校验范围，并携带预设确认规则启动", async () => {
    api.startTargetTask.mockResolvedValue(task() as never);
    render(<AutomaticTaskPanel workspaceId="ws-1" threadId="thread-1" />);
    await waitFor(() => expect(api.listTargetTasks).toHaveBeenCalledWith("ws-1"));
    fireEvent.click(screen.getByRole("button", { name: "全自动任务" }));
    fireEvent.change(screen.getByLabelText("任务目标"), { target: { value: "chapter_outlines" } });
    fireEvent.change(screen.getByLabelText("目标卷号"), { target: { value: "1,2,3" } });
    fireEvent.change(screen.getByLabelText("预设确认规则"), { target: { value: "按现有设定推进；普通取舍由 Agent 决定。" } });
    fireEvent.click(screen.getByRole("button", { name: "开始全自动任务" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("四个");
    expect(api.startTargetTask).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("目标卷号"), { target: { value: "1,2,3,4" } });
    fireEvent.click(screen.getByRole("button", { name: "开始全自动任务" }));
    await waitFor(() => expect(api.startTargetTask).toHaveBeenCalledWith("ws-1", {
      target: { task_type: "chapter_outlines", volume_ids: [1, 2, 3, 4] },
      confirmation_rules: "按现有设定推进；普通取舍由 Agent 决定。", thread_id: "thread-1",
      billing_protocol: 2, message_id: expect.any(String),
    }));
  });

  async function openTextTask() {
    render(<AutomaticTaskPanel workspaceId="ws-1" threadId="thread-1" />);
    await waitFor(() => expect(api.listTargetTasks).toHaveBeenCalledWith("ws-1"));
    fireEvent.click(screen.getByRole("button", { name: "全自动任务" }));
    fireEvent.change(screen.getByLabelText("任务目标"), { target: { value: "chapter_text" } });
    fireEvent.change(screen.getByLabelText("目标章号"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("预设确认规则"), { target: { value: "按固定资料确认并完成审稿修订" } });
  }

  it("全自动任务提交当前可见预算和协议2，并刷新账户", async () => {
    useBillingStore.setState({ account: account(true) });
    api.fetchMyBilling.mockResolvedValue(account(true));
    api.startTargetTask.mockResolvedValue(task() as never);
    await openTextTask();
    fireEvent.change(screen.getByLabelText("本次最多使用积分"), { target: { value: "12.34" } });
    fireEvent.click(screen.getByRole("button", { name: "开始全自动任务" }));
    await waitFor(() => expect(api.startTargetTask).toHaveBeenCalled());
    expect(api.startTargetTask.mock.calls[0][1]).toMatchObject({
      billing_protocol: 2, billing_budget: 12.34, message_id: expect.any(String),
      target: { task_type: "chapter_text", volume_ids: [], chapter: 1 },
    });
    expect(api.fetchMyBilling).toHaveBeenCalled();
  });

  it("预算超过余额时沿原预算校验拦截，不启动任务", async () => {
    useBillingStore.setState({ account: account(true, 5) });
    api.fetchMyBilling.mockResolvedValue(account(true, 5));
    await openTextTask();
    fireEvent.click(screen.getByRole("button", { name: "开始全自动任务" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("预算超过可用积分");
    expect(api.startTargetTask).not.toHaveBeenCalled();
  });

  it("响应丢失后相同输入预算复用消息ID，即使预算已经预留", async () => {
    useBillingStore.setState({ account: account(true) });
    api.fetchMyBilling.mockResolvedValue(account(true));
    api.startTargetTask.mockRejectedValueOnce(new Error("请求结果暂未确认")).mockResolvedValue(task() as never);
    await openTextTask();
    fireEvent.change(screen.getByLabelText("本次最多使用积分"), { target: { value: "12.34" } });
    fireEvent.click(screen.getByRole("button", { name: "开始全自动任务" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("请求结果暂未确认");
    const first = api.startTargetTask.mock.calls[0][1];
    const reserved = { ...account(true, 2), reserved: 12.34, total_balance: 14.34 };
    useBillingStore.setState({ account: reserved });
    api.fetchMyBilling.mockResolvedValue(reserved);
    fireEvent.click(screen.getByRole("button", { name: "开始全自动任务" }));
    await waitFor(() => expect(api.startTargetTask).toHaveBeenCalledTimes(2));
    expect(first.message_id).toEqual(expect.any(String));
    expect(api.startTargetTask.mock.calls[1][1]).toEqual(first);
  });

  it("更改预算后属于新操作，不复用上次失败请求的消息ID", async () => {
    useBillingStore.setState({ account: account(true) });
    api.fetchMyBilling.mockResolvedValue(account(true));
    api.startTargetTask.mockRejectedValueOnce(new Error("请求结果暂未确认")).mockResolvedValue(task() as never);
    await openTextTask();
    fireEvent.change(screen.getByLabelText("本次最多使用积分"), { target: { value: "12.34" } });
    fireEvent.click(screen.getByRole("button", { name: "开始全自动任务" }));
    await screen.findByRole("alert");
    fireEvent.change(screen.getByLabelText("本次最多使用积分"), { target: { value: "15" } });
    fireEvent.click(screen.getByRole("button", { name: "开始全自动任务" }));
    await waitFor(() => expect(api.startTargetTask).toHaveBeenCalledTimes(2));
    const [first, second] = api.startTargetTask.mock.calls.map((call) => call[1]);
    expect(second.billing_budget).toBe(15);
    expect(second.message_id).not.toBe(first.message_id);
  });

  it("主动收起再打开属于新提交，相同输入预算也使用新消息ID", async () => {
    useBillingStore.setState({ account: account(true) });
    api.fetchMyBilling.mockResolvedValue(account(true));
    api.startTargetTask.mockRejectedValueOnce(new Error("作品资料检查失败")).mockResolvedValue(task() as never);
    await openTextTask();
    fireEvent.change(screen.getByLabelText("本次最多使用积分"), { target: { value: "12.34" } });
    fireEvent.click(screen.getByRole("button", { name: "开始全自动任务" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("作品资料检查失败");
    fireEvent.click(screen.getByRole("button", { name: "收起" }));
    fireEvent.click(screen.getByRole("button", { name: "全自动任务" }));
    fireEvent.change(screen.getByLabelText("本次最多使用积分"), { target: { value: "12.34" } });
    fireEvent.click(screen.getByRole("button", { name: "开始全自动任务" }));
    await waitFor(() => expect(api.startTargetTask).toHaveBeenCalledTimes(2));
    const [first, second] = api.startTargetTask.mock.calls.map((call) => call[1]);
    expect(second).toEqual({ ...first, message_id: expect.any(String) });
    expect(second.message_id).not.toBe(first.message_id);
  });

  it("已运行任务可停止，停止请求不冒充已停止", async () => {
    api.listTargetTasks.mockResolvedValue({ tasks: [task() as never] });
    api.stopTargetTask.mockResolvedValue(task({ status: "stopping" }) as never);
    const onBusyChange = vi.fn();
    render(<AutomaticTaskPanel workspaceId="ws-1" threadId="thread-1" onBusyChange={onBusyChange} />);
    fireEvent.click(await screen.findByRole("button", { name: "停止全自动任务" }));
    await waitFor(() => expect(api.stopTargetTask).toHaveBeenCalledWith("ws-1", "target-1"));
    expect(await screen.findByText("停止中")).toBeInTheDocument();
    expect(onBusyChange).toHaveBeenCalledWith(true);
  });

  it("失败保留原因和已生成产物，不显示完成", async () => {
    api.listTargetTasks.mockResolvedValue({ tasks: [task({
      status: "failed", error: "目标第4卷缺少卷纲", artifacts: { "章纲/第一章.md": "已经保存的章纲" },
    }) as never] });
    render(<AutomaticTaskPanel workspaceId="ws-1" threadId="thread-1" />);
    expect(await screen.findByText("目标第4卷缺少卷纲")).toBeInTheDocument();
    expect(screen.getByText("章纲/第一章.md")).toBeInTheDocument();
    expect(screen.queryByText("已完成")).not.toBeInTheDocument();
  });

  it("切换作品后忽略旧作品迟到的任务响应", async () => {
    let resolveOld: (value: { tasks: never[] }) => void = () => {};
    api.listTargetTasks.mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve; }));
    const { rerender } = render(<AutomaticTaskPanel workspaceId="ws-old" threadId="thread-old" />);
    rerender(<AutomaticTaskPanel workspaceId="ws-new" threadId="thread-new" />);
    await waitFor(() => expect(api.listTargetTasks).toHaveBeenCalledWith("ws-new"));
    resolveOld({ tasks: [task({ error: "旧作品信息" }) as never] });
    await waitFor(() => expect(screen.queryByText("旧作品信息")).not.toBeInTheDocument());
  });
});
