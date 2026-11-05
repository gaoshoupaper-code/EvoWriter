import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AutomaticTaskPanel } from "@/components/workspace/AutomaticTaskPanel";
import * as apiModule from "@/lib/api";

vi.mock("@/lib/api", () => ({
  listTargetTasks: vi.fn(), startTargetTask: vi.fn(),
  getTargetTask: vi.fn(), stopTargetTask: vi.fn(),
}));
vi.mock("@/lib/usePanelPolling", () => ({ refreshWorkspaceContent: vi.fn() }));

const api = vi.mocked(apiModule);
const task = (overrides: Record<string, unknown> = {}) => ({
  task_id: "target-1", status: "running", phase: "chapter", completed: false,
  trace_ids: ["trace-1"], artifacts: {}, checks: {}, usage: {},
  input_revision: "input-1", source_commit: "abc1234", attempts: 1,
  created_at: "2026-11-06T01:00:00Z", updated_at: "2026-11-06T01:00:00Z",
  ...overrides,
});

beforeEach(() => {
  vi.clearAllMocks();
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
    }));
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
