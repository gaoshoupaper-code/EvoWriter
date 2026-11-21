import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "@/App";

function response(status: number, value: unknown) {
  return { status, headers: {}, body: JSON.stringify(value) };
}

function assets(suite: string) {
  return {
    system_prompt: `# ${suite}\n\n保留 **原文**`,
    tools: [{ name: `${suite}_tool`, description: `查阅 ${suite} 的资料。` }],
  };
}

let loadAssets: (suite: string) => ReturnType<typeof response> | Promise<ReturnType<typeof response>>;

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState(null, "", "#/bench?agent=outline");
  loadAssets = (suite) => response(200, assets(suite));
  vi.mocked(listen).mockResolvedValue(() => {});
  vi.mocked(invoke).mockReset().mockImplementation(async (command, args) => {
    if (command !== "http_request") return null;
    const { path } = (args as { request: { path: string } }).request;
    if (path === "/api/auth/me") {
      return response(200, { user_id: "member-123", username: "普通用户", is_admin: false, is_super_admin: false });
    }
    const endpoint = /^\/evolution-api\/([^/]+)\/api\/experiments\/evolver-assets$/.exec(path);
    if (endpoint) return loadAssets(endpoint[1]);
    if (path.endsWith("/api/experiments/options")) {
      return response(200, { versions: [], models: [], judge_groups: [], production_version: null });
    }
    return response(200, { items: [] });
  });
});

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

async function openAssets() {
  render(<App />);
  const sidebar = within(await screen.findByRole("complementary"));
  const link = sidebar.getByRole("link", { name: "进化 Agent 资产" });
  fireEvent.click(link);
  await screen.findByRole("heading", { name: "进化 Agent 资产" });
  expect(link).toHaveAttribute("aria-current", "page");
  expect(sidebar.getByRole("link", { name: "评测工作台" })).not.toHaveAttribute("aria-current", "page");
  return within(screen.getByRole("navigation", { name: "评测对象" }));
}

it("shows the literal prompt and tool purposes for all four suites to a regular user", async () => {
  await openAssets();
  for (const [name, key] of [["总纲", "outline"], ["卷纲", "volume"], ["章纲", "chapter"], ["正文", "text"]]) {
    const suites = within(screen.getByRole("navigation", { name: "评测对象" }));
    const suite = suites.getByRole("link", { name });
    if (suite.getAttribute("aria-current") !== "true") fireEvent.click(suite);
    const prompt = await screen.findByText(/保留 \*\*原文\*\*/);
    expect(prompt.textContent).toBe(assets(key).system_prompt);
    fireEvent.mouseDown(screen.getByRole("tab", { name: "工具" }), { button: 0, ctrlKey: false });
    expect(await screen.findByText(`${key}_tool`)).toBeVisible();
    expect(screen.getByText(`查阅 ${key} 的资料。`)).toBeVisible();
  }
});

it("retries the selected suite and ignores a slower response from the previous suite", async () => {
  let finishVolume!: (value: ReturnType<typeof response>) => void;
  const volume = new Promise<ReturnType<typeof response>>((resolve) => { finishVolume = resolve; });
  let attempts = 0;
  loadAssets = (suite) => {
    if (suite === "volume") return volume;
    if (suite === "text" && ++attempts === 1) return response(500, { detail: "配置暂时无法读取" });
    return response(200, assets(suite));
  };
  const suites = await openAssets();
  await screen.findByText(/# outline/);

  fireEvent.click(suites.getByRole("link", { name: "卷纲" }));
  expect(screen.getByRole("status")).toHaveTextContent("正在加载");
  expect(screen.queryByText(/# outline/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("link", { name: "正文" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("配置暂时无法读取");
  fireEvent.click(screen.getByRole("button", { name: "重试" }));

  expect(await screen.findByText(/# text/)).toBeVisible();
  expect(attempts).toBe(2);
  await act(async () => finishVolume(response(200, assets("volume"))));
  await waitFor(() => expect(screen.getByText(/# text/)).toBeVisible());
  expect(screen.queryByText(/# volume/)).not.toBeInTheDocument();
});
