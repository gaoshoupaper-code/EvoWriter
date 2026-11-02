import { beforeEach, describe, expect, it, vi } from "vitest";
import { useWorkspaceStore } from "@/stores/workspace";
import { fetchWorkspaceBootstrap } from "@/lib/api";

vi.mock("@/lib/api", () => ({ fetchWorkspaceBootstrap: vi.fn() }));

beforeEach(() => { vi.resetAllMocks(); useWorkspaceStore.setState({ activeWorkspaceId: "A", activeThreadId: "", threads: [] }); });

describe("人物列表读取失败的工作区归属", () => {
  it("真实的当前作品加载失败回调携带原workspaceId", async () => {
    const report = vi.fn();
    vi.mocked(fetchWorkspaceBootstrap).mockRejectedValueOnce(new Error("离线"));
    expect(await useWorkspaceStore.getState().switchWorkspace("A", report)).toBeNull();
    expect(report).toHaveBeenCalledExactlyOnceWith("A", "离线");
  });

  it("切书后的旧请求失败不回调新作品的错误", async () => {
    let rejectOld!: (cause: Error) => void;
    const report = vi.fn();
    vi.mocked(fetchWorkspaceBootstrap).mockImplementationOnce(() => new Promise((_, reject) => { rejectOld = reject; }));
    const old = useWorkspaceStore.getState().switchWorkspace("A", report);
    useWorkspaceStore.setState({ activeWorkspaceId: "B" });
    rejectOld(new Error("旧请求离线"));
    await old;
    expect(report).not.toHaveBeenCalled();
  });
});
