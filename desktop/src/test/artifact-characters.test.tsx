import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ArtifactPanel } from "@/components/workspace/ArtifactPanel";
import { deleteArtifactForRedo } from "@/lib/api";
import { refreshWorkspaceContent } from "@/lib/usePanelPolling";
import { setExecutionDeps, useExecutionStore } from "@/stores/execution";
import { useWorkspaceStore } from "@/stores/workspace";
import { streamRequest } from "@/lib/stream";

vi.mock("@/lib/api", () => ({ deleteArtifactForRedo: vi.fn(), deleteArtifactsBatch: vi.fn(), fetchAnnotations: vi.fn(async () => ({ annotations: [] })), dispatchAnnotations: vi.fn(async () => ({ message: "修改正文标注", invalidated: 0 })) }));
vi.mock("@/lib/stream", () => ({ streamRequest: vi.fn() }));
vi.mock("@/lib/usePanelPolling", () => ({ refreshWorkspaceContent: vi.fn(async () => true), watchPipelineAfterDisconnect: vi.fn() }));
vi.mock("@/components/workspace/PipelineStageBar", () => ({ PipelineStageBar: () => null }));
vi.mock("@/components/workspace/ManuscriptEditor", () => ({ ManuscriptEditor: () => null }));
vi.mock("@/components/workspace/annotations/AnnotationHost", () => ({ AnnotationHost: ({ children }: { children: ReactNode }) => children }));
vi.mock("@/components/workspace/annotations/AnnotationDrawer", () => ({ AnnotationDrawer: ({ open, onDispatch }: { open: boolean; onDispatch: () => void }) => open ? <button onClick={onDispatch}>把标注交给陪练</button> : null }));
vi.mock("@/components/workspace/TrashDialog", () => ({ TrashDialog: () => null }));

const files = [{ rel: "人物/主角.md", markdown: "# 主角旧稿" }, { rel: "正文/第001章开局.md", markdown: "正文保留" }];
function open() { return render(<ArtifactPanel kind="five" pipeline={null} files={files} activeRel="人物/主角.md" onSelect={vi.fn()} workspaceId="ws-1" />); }

beforeEach(() => {
  vi.clearAllMocks();
  useExecutionStore.setState({ loading: false, messages: [], characterRepair: false, characterRepairWorkspaceId: "" });
  vi.mocked(deleteArtifactForRedo).mockResolvedValue({ status: "ok", deleted: "人物/主角.md", trash_rel: "trash", removed_annotations: 0, stage_reset_to: null, stage_label: "正文", pipeline: { kind: "five", phase: "chapter", next_unit: "章纲", awaiting: {} } });
});

describe("人物卡删除重做（AC-002/008）", () => {
  it("从补卡模式打开正文派发标注，实际请求仍走普通写作", async () => {
    useWorkspaceStore.setState({ activeWorkspaceId: "ws-1", activeThreadId: "thread-1", hasApiKey: true });
    setExecutionDeps({
      getActiveThreadId: () => "thread-1", getActiveWorkspaceId: () => "ws-1", getActiveThreadSessionName: () => "正文", getActiveThreadWorkspacePath: () => "/test",
      setActiveThreadId: vi.fn(), setThreads: vi.fn(), addThread: vi.fn(), getTraceRuns: () => [], getActiveTraceId: () => "", getLiveTraceId: () => "",
      setTraceRuns: vi.fn(), setTraceDetail: vi.fn(), setActiveTraceId: vi.fn(), setLiveTraceId: vi.fn(),
    });
    let emitted = false;
    vi.mocked(streamRequest).mockResolvedValue({
      read: async () => {
        if (emitted) return { done: true, value: undefined };
        emitted = true;
        return { done: false, value: new TextEncoder().encode('event: final\ndata: {"mode":"screenplay","content":"正文已修订"}\n\n') };
      }, cancel: async () => {},
    });
    useExecutionStore.getState().setCharacterRepair(true);
    render(<ArtifactPanel kind="five" pipeline={null} files={[{ rel: "正文/第001章开局.md", markdown: "正文待修改" }]} activeRel="正文/第001章开局.md" onSelect={vi.fn()} workspaceId="ws-1" />);
    await userEvent.click(screen.getByRole("button", { name: "标注 0 条" }));
    await userEvent.click(screen.getByRole("button", { name: "把标注交给陪练" }));
    await waitFor(() => expect(streamRequest).toHaveBeenCalled());
    expect(vi.mocked(streamRequest).mock.calls[0][1]?.body).toMatchObject({ prompt: "修改正文标注" });
    expect(vi.mocked(streamRequest).mock.calls[0][1]?.body).not.toHaveProperty("character_repair", true);
    expect(useExecutionStore.getState().characterRepair).toBe(false);
  });
  it("二次确认说明保留进度和主角缺卡保护；取消不删除", async () => {
    open();
    await userEvent.click(screen.getByRole("button", { name: "删除重做" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("写作进度和已有纲文保留");
    expect(dialog).toHaveTextContent("主角卡补齐前");
    expect(dialog).not.toHaveTextContent("流水线回到");
    await userEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(deleteArtifactForRedo).not.toHaveBeenCalled();
    expect(screen.getByText("主角旧稿")).toBeInTheDocument();
  });

  it("仅删除选中的人物卡，刷新排在旧轮询之后且不自动生成", async () => {
    open();
    await userEvent.click(screen.getByRole("button", { name: "删除重做" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "删除重做" }));
    await waitFor(() => expect(refreshWorkspaceContent).toHaveBeenCalledWith("ws-1", true));
    expect(deleteArtifactForRedo).toHaveBeenCalledExactlyOnceWith("ws-1", "人物/主角.md");
    expect(files[1].markdown).toBe("正文保留");
  });

  it("删除失败保留卡片，生成中禁用删除入口", async () => {
    vi.mocked(deleteArtifactForRedo).mockRejectedValueOnce(new Error("删除失败"));
    const { rerender } = open();
    await userEvent.click(screen.getByRole("button", { name: "删除重做" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "删除重做" }));
    await waitFor(() => expect(within(screen.getByRole("dialog")).getByRole("button", { name: "删除重做" })).not.toBeDisabled());
    expect(screen.getByText("主角旧稿")).toBeInTheDocument();
    expect(refreshWorkspaceContent).not.toHaveBeenCalled();
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "取消" }));
    act(() => useExecutionStore.setState({ loading: true }));
    rerender(<ArtifactPanel kind="five" pipeline={null} files={files} activeRel="人物/主角.md" onSelect={vi.fn()} workspaceId="ws-1" />);
    expect(screen.getByRole("button", { name: "删除重做" })).toBeDisabled();
  });
});
