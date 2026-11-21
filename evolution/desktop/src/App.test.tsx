import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import App from "./App";

let signedIn = true;
const paths: string[] = [];
let pendingSnapshot: Promise<unknown> | undefined;
const user = { user_id: "account-1234", username: "测试作者", is_admin: false, is_super_admin: true };

beforeEach(() => {
  signedIn = true;
  paths.length = 0;
  localStorage.clear();
  pendingSnapshot = undefined;
  vi.mocked(listen).mockResolvedValue(() => {});
  vi.stubEnv("DEV", false);
  vi.mocked(invoke).mockImplementation(async (command, args) => {
    if (command === "check_update") return { available: true, version: "1.2.3", current_version: "1.2.2" };
    if (command !== "http_request") return null;
    const path = (args as { request: { path: string } }).request.path;
    paths.push(path);
    const reply = (status: number, value: unknown) => ({ status, headers: {}, body: JSON.stringify(value) });
    if (path === "/api/auth/me") return signedIn ? reply(200, user) : reply(401, {});
    if (path === "/api/auth/login") { signedIn = true; return reply(200, user); }
    if (path === "/evolution-api/outline/api/snapshots" && pendingSnapshot) return pendingSnapshot;
    if (path.endsWith("/api/snapshots") || path === "/api/admin/users") return reply(200, []);
    return reply(404, { detail: "测试记录不存在" });
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  window.history.replaceState(null, "", "/");
});

it.each([["writing", "写作详情"], ["evaluation", "评分详情"]])("opens a fresh record deep link for %s in the text suite", async (section, title) => {
  const url = "#/bench/experiments/archive/records/record-one/" + section + "?agent=text";
  window.history.replaceState(null, "", url);
  render(<App />);
  expect(await screen.findByRole("heading", { name: title })).toBeVisible();
  await waitFor(() => expect(paths).toContain("/evolution-api/text/api/experiments/runs/archive"));
  expect(window.location.hash).toBe(url);
});

it.each([["rounds/1", "进化详情"], ["review", "实验审核"], ["comparison", "对比详情"]])(
  "opens the experiment %s page in its original suite", async (page, title) => {
    const url = "#/bench/experiments/archive/" + page + "?agent=text";
    window.history.replaceState(null, "", url);
    render(<App />);
    expect(await screen.findByRole("heading", { name: title })).toBeVisible();
    await waitFor(() => expect(paths).toContain("/evolution-api/text/api/experiments/runs/archive"));
    expect(window.location.hash).toBe(url);
  },
);

it("opens the original dashboard, with its account, navigation and update controls", async () => {
  render(<App />);
  expect(await screen.findByRole("heading", { name: "用户运行" })).toBeInTheDocument();
  const suites = within(screen.getByRole("main")).getByRole("navigation", { name: "运行记录所属 Agent" });
  expect(within(suites).getAllByRole("link").map(link => link.textContent)).toEqual(["总纲", "卷纲", "章纲", "正文"]);
  expect(within(suites).getByRole("link", { name: "总纲" })).toHaveAttribute("aria-current", "true");
  expect(within(screen.getByRole("complementary")).queryByText("总纲")).not.toBeInTheDocument();
  expect(screen.getByText("测试作者")).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "界面字号" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "立即更新" })).toBeInTheDocument();
});

it("remembers each feature independently and keeps account administration global", async () => {
  render(<App />);
  const sidebar = within(await screen.findByRole("complementary"));
  fireEvent.click(sidebar.getByRole("link", { name: "Harness 资产" }));
  const assets = await screen.findByRole("navigation", { name: "Harness 资产所属 Agent" });
  fireEvent.click(within(assets).getByRole("link", { name: "正文" }));
  await waitFor(() => expect(paths).toContain("/evolution-api/text/api/snapshots"));
  expect(window.location.hash).toBe("#/harness?agent=text");
  fireEvent.click(sidebar.getByRole("link", { name: "实验记录" }));
  const evaluation = await screen.findByRole("navigation", { name: "评测对象" });
  expect(within(evaluation).getByRole("link", { name: "总纲" })).toHaveAttribute("aria-current", "true");
  fireEvent.click(within(evaluation).getByRole("link", { name: "卷纲" }));
  await waitFor(() => expect(paths).toContain("/evolution-api/volume/api/experiments/runs"));
  fireEvent.click(sidebar.getByRole("link", { name: "评测规则" }));
  await waitFor(() => expect(paths.some(path => path.startsWith("/evolution-api/volume/api/experiments/rules"))).toBe(true));
  fireEvent.click(sidebar.getByRole("link", { name: "Harness 资产" }));
  expect(await screen.findByRole("link", { name: "正文" })).toHaveAttribute("aria-current", "true");
  fireEvent.click(sidebar.getByRole("link", { name: "管理后台" }));
  await waitFor(() => expect(paths).toContain("/api/admin/users"));
  expect(screen.queryByRole("link", { name: "正文" })).not.toBeInTheDocument();
  expect(paths.some(path => path.includes("/api/admin/") && path.startsWith("/evolution-api/"))).toBe(false);
});

it("resolves old bookmarks in the default suite and retains the page and query", async () => {
  window.history.replaceState(null, "", "#/bench/experiments?tab=all");
  render(<App />);
  expect(await screen.findByRole("heading", { name: "实验记录" })).toBeInTheDocument();
  expect(window.location.hash).toBe("#/bench/experiments?tab=all");
  expect(paths.some(path => path.startsWith("/evolution-api/outline/"))).toBe(true);
});

it("ignores a previous agent's late unauthorized response after switching the open feature", async () => {
  let finish!: (response: unknown) => void;
  pendingSnapshot = new Promise(resolve => { finish = resolve; });
  window.history.replaceState(null, "", "#/harness");
  render(<App />);
  fireEvent.click(await screen.findByRole("link", { name: "正文" }));
  await waitFor(() => expect(paths).toContain("/evolution-api/text/api/snapshots"));
  await act(async () => { finish({ status: 401, headers: {}, body: "上个套别的错误" }); });
  expect(screen.queryByText(/上个套别的错误/)).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "正文" })).toHaveAttribute("aria-current", "true");
});

it("pins private detail bookmarks to their owner and drops private IDs on a switch", async () => {
  localStorage.setItem("evolution.agent.evaluation", "volume");
  window.history.replaceState(null, "", "#/bench/experiments/volume-run?tab=traces");
  render(<App />);
  await waitFor(() => expect(window.location.hash).toBe("#/bench/experiments/volume-run?tab=traces&agent=volume"));
  await waitFor(() => expect(paths).toContain("/evolution-api/volume/api/experiments/runs/volume-run"));
  fireEvent.click(await screen.findByRole("link", { name: "章纲" }));
  await waitFor(() => expect(paths).toContain("/evolution-api/chapter/api/experiments/runs"));
  expect(window.location.hash).toBe("#/bench/experiments?agent=chapter");
  expect(paths).not.toContain("/evolution-api/chapter/api/experiments/runs/volume-run");
});

it("preserves the requested scoped page through login and a subsequent refresh", async () => {
  signedIn = false;
  window.history.replaceState(null, "", "#/agents/chapter/harness");
  const root = render(<App />);
  expect(await screen.findByRole("button", { name: "登录" })).toBeInTheDocument();
  expect(window.location.hash).toBe("#/login");
  expect(screen.getByRole("button", { name: "立即更新" })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "测试作者" } });
  fireEvent.change(screen.getByLabelText("密码"), { target: { value: "fixture" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await waitFor(() => expect(paths).toContain("/evolution-api/chapter/api/snapshots"));
  expect(window.location.hash).toBe("#/harness?agent=chapter");
  root.unmount();
  render(<App />);
  await waitFor(() => expect(paths.filter(path => path === "/evolution-api/chapter/api/snapshots")).toHaveLength(2));
  expect(screen.getByRole("link", { name: "章纲" })).toHaveAttribute("aria-current", "true");
});
