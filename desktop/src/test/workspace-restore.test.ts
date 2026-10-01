/**
 * workspace store 回现场与删除联动测试（REQ-20261001-170627 FR-001/FR-003）。
 *
 * mock @/lib/session-persist，验证：
 * - bootstrap 按本地记忆选工作区与会话；失效逐级回退（FR-001）
 * - 删除会话/工作区联动清理本地记录（FR-003）
 * - 会话切换落位记忆（FR-001）
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const persistMock = {
  initSessionPersist: vi.fn(),
  readLastPosition: vi.fn(),
  saveLastPosition: vi.fn(),
  deleteThreadRecord: vi.fn(),
  deleteWorkspaceRecords: vi.fn(),
};

vi.mock("@/lib/session-persist", () => persistMock);

const ws1 = { workspace_id: "ws-1", title: "一", domain: "writing", workspace_path: "/1", created_at: "", updated_at: "", session_count: 0, active_style_id: null };
const ws2 = { workspace_id: "ws-2", title: "二", domain: "writing", workspace_path: "/2", created_at: "", updated_at: "", session_count: 0, active_style_id: null };
const th1 = { thread_id: "th-1", workspace_id: "ws-1", session_name: "a", workspace_path: "/1", created_at: "", updated_at: "" };
const th2 = { thread_id: "th-2", workspace_id: "ws-2", session_name: "b", workspace_path: "/2", created_at: "", updated_at: "" };

const apiMock = {
  fetchMeOrNull: vi.fn().mockResolvedValue({ username: "u", is_admin: false, has_api_key: true }),
  fetchInit: vi.fn(),
  fetchWorkspaceBootstrap: vi.fn(),
  fetchWorkspaces: vi.fn(),
  deleteThread: vi.fn().mockResolvedValue({}),
  deleteWorkspace: vi.fn().mockResolvedValue({}),
  createThread: vi.fn(),
  createWorkspace: vi.fn(),
  updateThread: vi.fn(),
  activateStyle: vi.fn(), createStyle: vi.fn(), updateStyle: vi.fn(), deleteStyle: vi.fn(), optimizeStyle: vi.fn(),
  logout: vi.fn(),
};

vi.mock("@/lib/api", () => apiMock);

const { useWorkspaceStore } = await import("@/stores/workspace");

beforeEach(async () => {
  vi.clearAllMocks();
  const { resetStores } = await import("./helpers");
  await resetStores();
});

describe("bootstrap 回现场（FR-001 / AC-001）", () => {
  it("本地记忆的工作区与会话仍存在 → 直接落位并保存", async () => {
    apiMock.fetchInit.mockResolvedValue({ workspaces: [ws1, ws2], styles: [] });
    apiMock.fetchWorkspaceBootstrap.mockResolvedValue({
      threads: [th2, { ...th2, thread_id: "th-2b" }], storyline: null, characters: null, worldview: null,
    });
    persistMock.readLastPosition.mockResolvedValue({ workspaceId: "ws-2", threadId: "th-2b" });

    await useWorkspaceStore.getState().bootstrap();

    const s = useWorkspaceStore.getState();
    expect(s.activeWorkspaceId).toBe("ws-2");
    expect(s.activeThreadId).toBe("th-2b");
    expect(persistMock.saveLastPosition).toHaveBeenCalledWith("ws-2", "th-2b");
  });

  it("记忆的工作区已被删除 → 回退第一个工作区的第一个会话", async () => {
    apiMock.fetchInit.mockResolvedValue({ workspaces: [ws1], styles: [] });
    apiMock.fetchWorkspaceBootstrap.mockResolvedValue({ threads: [th1], storyline: null, characters: null, worldview: null });
    persistMock.readLastPosition.mockResolvedValue({ workspaceId: "ws-gone", threadId: "th-x" });

    await useWorkspaceStore.getState().bootstrap();

    const s = useWorkspaceStore.getState();
    expect(s.activeWorkspaceId).toBe("ws-1");
    expect(s.activeThreadId).toBe("th-1");
    expect(persistMock.saveLastPosition).toHaveBeenCalledWith("ws-1", "th-1");
  });

  it("记忆的会话不在该工作区名单 → 回退第一个会话", async () => {
    apiMock.fetchInit.mockResolvedValue({ workspaces: [ws1], styles: [] });
    apiMock.fetchWorkspaceBootstrap.mockResolvedValue({ threads: [th1], storyline: null, characters: null, worldview: null });
    persistMock.readLastPosition.mockResolvedValue({ workspaceId: "ws-1", threadId: "th-gone" });

    await useWorkspaceStore.getState().bootstrap();

    expect(useWorkspaceStore.getState().activeThreadId).toBe("th-1");
  });

  it("本地无位置记录 → 现状默认行为", async () => {
    apiMock.fetchInit.mockResolvedValue({ workspaces: [ws1], styles: [] });
    apiMock.fetchWorkspaceBootstrap.mockResolvedValue({ threads: [th1], storyline: null, characters: null, worldview: null });
    persistMock.readLastPosition.mockResolvedValue(null);

    await useWorkspaceStore.getState().bootstrap();

    expect(useWorkspaceStore.getState().activeWorkspaceId).toBe("ws-1");
    expect(useWorkspaceStore.getState().activeThreadId).toBe("th-1");
  });
});

describe("删除联动（FR-003 / AC-003）", () => {
  it("删除会话 → 联动删本地该会话记录", async () => {
    useWorkspaceStore.setState({
      activeWorkspaceId: "ws-1",
      threads: [th1],
      activeThreadId: "th-1",
      pendingDeleteWorkspaceId: "",
    });

    await useWorkspaceStore.getState().handleDeleteThread("th-1");

    expect(apiMock.deleteThread).toHaveBeenCalledWith("th-1");
    expect(persistMock.deleteThreadRecord).toHaveBeenCalledWith("ws-1", "th-1");
  });

  it("删除工作区 → 联动清该工作区全部本地记录", async () => {
    useWorkspaceStore.setState({
      workspaces: [ws1, ws2],
      activeWorkspaceId: "ws-1",
      pendingDeleteWorkspaceId: "ws-1",
    });

    await useWorkspaceStore.getState().handleDeleteWorkspace();

    expect(apiMock.deleteWorkspace).toHaveBeenCalledWith("ws-1");
    expect(persistMock.deleteWorkspaceRecords).toHaveBeenCalledWith("ws-1");
    expect(useWorkspaceStore.getState().activeWorkspaceId).toBe("ws-2");
  });
});

describe("位置记忆（FR-001）", () => {
  it("setActiveThreadId 落位后保存上次位置", () => {
    useWorkspaceStore.setState({ activeWorkspaceId: "ws-1", activeThreadId: "" });
    useWorkspaceStore.getState().setActiveThreadId("th-9");
    expect(persistMock.saveLastPosition).toHaveBeenCalledWith("ws-1", "th-9");
  });

  it("handleSelectThread 切换会话保存上次位置", () => {
    useWorkspaceStore.setState({ activeWorkspaceId: "ws-1", activeThreadId: "th-1", threads: [th1] });
    useWorkspaceStore.getState().handleSelectThread("th-2");
    expect(persistMock.saveLastPosition).toHaveBeenCalledWith("ws-1", "th-2");
  });
});
