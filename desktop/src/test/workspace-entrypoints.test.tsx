/** REQ-20261018-202005：真实 Home/顶栏/侧栏/菜单接线；只隔离无关面板和启动 IO。 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import Home from "@/pages/home";
import { logout } from "@/lib/api";
import { useWorkspaceStore } from "@/stores/workspace";
import { useExecutionStore } from "@/stores/execution";
import { useContentStore } from "@/stores/content";
import { useStageSessionStore } from "@/stores/stage-session";
import { resetStores } from "./helpers";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  logout: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("@/lib/usePanelPolling", () => ({ usePanelPolling: () => {} }));
vi.mock("@/components/workspace/ChatPanel", () => ({
  ChatPanel: ({ prompt }: { prompt: string }) => <textarea aria-label="输入草稿" value={prompt} readOnly />,
}));
vi.mock("@/components/workspace/WorkRail", () => ({ WorkRail: () => null }));
vi.mock("@/components/workspace/ArtifactPanel", () => ({ ArtifactPanel: () => null }));
vi.mock("@/components/workspace/ConfirmDialog", () => ({ ConfirmDialog: () => null }));

const BOOK = {
  workspace_id: "ws-1", title: "保留作品", domain: "writing", workspace_path: "/test",
  created_at: "", updated_at: "", session_count: 0, active_style_id: null,
};

function renderHome() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/settings" element={<div>原配置页</div>} />
        <Route path="/login" element={<div>登录页</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

async function openThemeMenu(user: ReturnType<typeof userEvent.setup>) {
  const sidebar = screen.getByRole("complementary", { name: "作品侧栏" });
  await user.click(within(sidebar).getByRole("button", { name: "用户菜单" }));
  expect(screen.queryByRole("menuitemradio")).not.toBeInTheDocument();
  await user.click(screen.getByRole("menuitem", { name: "主题" }));
}

beforeEach(async () => {
  vi.clearAllMocks();
  localStorage.clear();
  await resetStores();
  useWorkspaceStore.setState({
    ...useWorkspaceStore.getInitialState(),
    authChecked: true,
    authUser: { username: "writer", is_admin: false },
    hasApiKey: true,
    workspaces: [BOOK], activeWorkspaceId: BOOK.workspace_id,
    checkAuth: vi.fn().mockResolvedValue(true),
    bootstrap: vi.fn().mockResolvedValue(null),
  });
  useStageSessionStore.setState({
    ...useStageSessionStore.getInitialState(),
    hydrateForWorkspace: vi.fn().mockResolvedValue(undefined),
  });
  useContentStore.setState({ outlinePipeline: null, outlineFiles: [{ rel: "主题.md", markdown: "已有产物" }] });
  useExecutionStore.setState({ prompt: "还没发送的草稿", messages: [{ role: "user", content: "已有消息" }] });
});

afterEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});

describe("左下入口迁移（AC-001/003/006）", () => {
  it("顶栏只留品牌，账户和独立齿轮位于侧栏导航外", () => {
    renderHome();
    const topbar = screen.getByRole("banner");
    expect(within(topbar).getByText("思衍")).toBeInTheDocument();
    expect(within(topbar).queryByRole("button")).not.toBeInTheDocument();
    expect(within(topbar).queryByRole("link")).not.toBeInTheDocument();
    expect(within(topbar).queryByRole("switch")).not.toBeInTheDocument();
    const sidebar = screen.getByRole("complementary", { name: "作品侧栏" });
    const nav = within(sidebar).getByRole("navigation");
    const account = within(sidebar).getByRole("button", { name: "用户菜单" });
    const settings = within(sidebar).getByRole("link", { name: "设置（API Key）" });
    expect(account).toHaveTextContent("writer");
    expect(nav).not.toContainElement(account);
    expect(nav).not.toContainElement(settings);
    expect(settings).toHaveAttribute("href", "/settings");
  });

  it.each([true, false])("账户菜单保留 Key 配置状态：%s", async (hasApiKey) => {
    useWorkspaceStore.setState({ hasApiKey });
    renderHome();
    const sidebar = screen.getByRole("complementary", { name: "作品侧栏" });
    await userEvent.click(within(sidebar).getByRole("button", { name: "用户菜单" }));
    expect(screen.getByText(hasApiKey ? "Key 已配置" : "Key 未配置")).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "退出登录" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "设置（API Key）" })).not.toBeInTheDocument();
  });

  it("缺少用户名时保留头像与用户名兜底", () => {
    useWorkspaceStore.setState({ authUser: null });
    renderHome();
    const sidebar = screen.getByRole("complementary", { name: "作品侧栏" });
    const account = within(sidebar).getByRole("button", { name: "用户菜单" });
    expect(account).toHaveTextContent("?");
    expect(account).toHaveTextContent("用户");
  });

  it("齿轮无需打开菜单，直接进入原配置页", async () => {
    renderHome();
    const sidebar = screen.getByRole("complementary", { name: "作品侧栏" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    await userEvent.click(within(sidebar).getByRole("link", { name: "设置（API Key）" }));
    expect(await screen.findByText("原配置页")).toBeInTheDocument();
    expect(useExecutionStore.getState().prompt).toBe("还没发送的草稿");
    expect(useWorkspaceStore.getState().hasApiKey).toBe(true);
  });

  it.each(["light", "dark"] as const)("主题子菜单仅浅/深；重复选择不切换：%s", async (theme) => {
    localStorage.setItem("writer-theme", theme);
    renderHome();
    // jsdom 没有布局坐标；跳过模拟移出子菜单，避免 Radix 提前关闭菜单。
    const user = userEvent.setup({ skipHover: true });
    await openThemeMenu(user);
    const selected = theme === "light" ? "浅色" : "深色";
    expect(screen.getAllByRole("menuitemradio")).toHaveLength(2);
    expect(screen.getByRole("menuitemradio", { name: selected })).toHaveAttribute("aria-checked", "true");
    expect(screen.queryByText(/跟随系统/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("menuitemradio", { name: selected }));
    expect(useWorkspaceStore.getState().theme).toBe(theme);
    expect(localStorage.getItem("writer-theme")).toBe(theme);
  });

  it("键盘可打开主题子菜单并选择深色", async () => {
    const user = userEvent.setup();
    renderHome();
    const sidebar = screen.getByRole("complementary", { name: "作品侧栏" });
    act(() => within(sidebar).getByRole("button", { name: "用户菜单" }).focus());
    await user.keyboard("{Enter}");
    expect(screen.getByRole("menuitem", { name: "主题" })).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("menuitemradio", { name: "浅色" })).toHaveFocus();
    await user.keyboard("{ArrowDown}{Enter}");
    expect(useWorkspaceStore.getState().theme).toBe("dark");
    expect(localStorage.getItem("writer-theme")).toBe("dark");
    expect(within(sidebar).getByRole("button", { name: "用户菜单" })).toHaveFocus();
  });

  it("切换主题保留数据，并通过原持久化逻辑在重挂载时恢复", async () => {
    // jsdom 没有布局坐标；跳过模拟移出子菜单，避免 Radix 提前关闭菜单。
    const user = userEvent.setup({ skipHover: true });
    const toggleTheme = vi.fn(useWorkspaceStore.getState().toggleTheme);
    useWorkspaceStore.setState({ toggleTheme });
    const view = renderHome();
    const messages = useExecutionStore.getState().messages;
    const files = useContentStore.getState().outlineFiles;
    await openThemeMenu(user);
    await user.click(screen.getByRole("menuitemradio", { name: "深色" }));
    expect(toggleTheme).toHaveBeenCalledTimes(1);
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(localStorage.getItem("writer-theme")).toBe("dark");
    expect(screen.getByRole("textbox", { name: "输入草稿" })).toHaveValue("还没发送的草稿");
    expect(useExecutionStore.getState().messages).toBe(messages);
    expect(useContentStore.getState().outlineFiles).toBe(files);
    expect(useWorkspaceStore.getState().workspaces).toEqual([BOOK]);
    expect(useWorkspaceStore.getState().activeWorkspaceId).toBe(BOOK.workspace_id);
    expect(useWorkspaceStore.getState().hasApiKey).toBe(true);
    view.unmount();
    act(() => useWorkspaceStore.setState({ theme: "light", themeReady: false }));
    renderHome();
    await waitFor(() => expect(useWorkspaceStore.getState().theme).toBe("dark"));
    await openThemeMenu(user);
    expect(screen.getByRole("menuitemradio", { name: "深色" })).toHaveAttribute("aria-checked", "true");
    await user.click(screen.getByRole("menuitemradio", { name: "浅色" }));
    expect(localStorage.getItem("writer-theme")).toBe("light");
  });

  it.each([false, true])("退出登录沿用原处理，接口失败也保留原跳转：%s", async (failed) => {
    if (failed) vi.mocked(logout).mockRejectedValueOnce(new Error("offline"));
    renderHome();
    const sidebar = screen.getByRole("complementary", { name: "作品侧栏" });
    await userEvent.click(within(sidebar).getByRole("button", { name: "用户菜单" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "退出登录" }));
    expect(await screen.findByText("登录页")).toBeInTheDocument();
    expect(logout).toHaveBeenCalledTimes(1);
  });
});
