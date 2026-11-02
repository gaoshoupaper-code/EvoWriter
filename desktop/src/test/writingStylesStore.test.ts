import { beforeEach, describe, expect, it, vi } from "vitest";
import { useWritingStylesStore } from "@/stores/writingStyles";
import { listWritingStyles, renameWritingStyle, saveWorkspaceWritingStyle, deleteWritingStyle } from "@/lib/api";

vi.mock("@/lib/api", () => ({ listWritingStyles: vi.fn(), renameWritingStyle: vi.fn(), saveWorkspaceWritingStyle: vi.fn(), deleteWritingStyle: vi.fn() }));
const asset = { style_id: "a", name: "冷静", content: "完整文风", created_at: "now", updated_at: "now" };

beforeEach(() => { vi.resetAllMocks(); useWritingStylesStore.getState().clear(); });

describe("个人文风库账号隔离", () => {
  it("加载失败独立于空库，并可重试", async () => {
    vi.mocked(listWritingStyles).mockRejectedValueOnce(new Error("网络失败")).mockResolvedValueOnce([]);
    await expect(useWritingStylesStore.getState().load()).rejects.toThrow("网络失败");
    expect(useWritingStylesStore.getState().error).toBe("网络失败");
    await useWritingStylesStore.getState().load();
    expect(useWritingStylesStore.getState()).toMatchObject({ assets: [], error: "", loaded: true });
  });

  it("账号清空后忽略旧账号的迟到读取和写入", async () => {
    let finishLoad!: (assets: typeof asset[]) => void;
    let finishSave!: (value: typeof asset) => void;
    vi.mocked(listWritingStyles).mockImplementation(() => new Promise((resolve) => { finishLoad = resolve; }));
    vi.mocked(saveWorkspaceWritingStyle).mockImplementation(() => new Promise((resolve) => { finishSave = resolve; }));
    const loading = useWritingStylesStore.getState().load();
    const saving = useWritingStylesStore.getState().save("ws", "冷静");
    useWritingStylesStore.getState().clear();
    finishLoad([asset]); finishSave(asset);
    await loading; expect(await saving).toBeNull();
    expect(useWritingStylesStore.getState().assets).toEqual([]);
  });

  it("保存使用服务端结果更新列表，失败保留旧资产", async () => {
    vi.mocked(saveWorkspaceWritingStyle).mockResolvedValue(asset);
    await useWritingStylesStore.getState().save("ws", "冷静");
    expect(useWritingStylesStore.getState().assets).toEqual([asset]);
    vi.mocked(renameWritingStyle).mockRejectedValue(new Error("同名"));
    await expect(useWritingStylesStore.getState().rename("a", "旧名")).rejects.toThrow("同名");
    expect(useWritingStylesStore.getState().assets).toEqual([asset]);
    vi.mocked(deleteWritingStyle).mockRejectedValue(new Error("删除失败"));
    await expect(useWritingStylesStore.getState().remove("a")).rejects.toThrow();
    expect(useWritingStylesStore.getState().assets).toEqual([asset]);
  });
});
