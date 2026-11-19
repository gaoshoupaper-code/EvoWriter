import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { toast } from "sonner";
import UpdateBanner from "./UpdateBanner";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn().mockResolvedValue(() => {}) }));
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));
afterEach(() => vi.unstubAllGlobals());

it("keeps one download across remounts, displays actual progress, and allows retry", async () => {
  vi.stubGlobal("confirm", () => true);
  let report!: (value: object) => void;
  let fail!: (reason: string) => void;
  const commands: string[] = [];
  vi.mocked(listen).mockImplementation(async (event, handler) => {
    if (event === "update_progress") report = value => handler({ event, id: 1, payload: value } as never);
    return () => {};
  });
  vi.mocked(invoke).mockImplementation(async (command) => {
    commands.push(command);
    if (command === "check_update") {
      return { available: true, version: "1.1", current_version: "1.0" };
    }
    return new Promise<never>((_, reject) => { fail = reject; });
  });
  const view = render(<UpdateBanner />);
  fireEvent.click(await screen.findByRole("button", { name: "立即更新" }));
  expect(screen.getByRole("button", { name: "正在连接下载…" })).toBeDisabled();
  await waitFor(() => expect(commands).toContain("install_update"));
  act(() => report({ phase: "downloading", downloaded: 1_500_000, total: null }));
  expect(screen.getByRole("button", { name: "已下载 1.5 MB" })).toBeDisabled();
  view.unmount();
  render(<UpdateBanner />);
  await screen.findByRole("button", { name: "已下载 1.5 MB" });
  act(() => report({ phase: "downloading", downloaded: 2_000_000, total: 10_000_000 }));
  expect(screen.getByRole("button", { name: "已下载 2.0 / 10.0 MB（20%）" })).toBeDisabled();
  act(() => report({ phase: "retrying" }));
  expect(screen.getByRole("button", { name: "正在切换为普通下载…" })).toBeDisabled();
  act(() => report({ phase: "installing" }));
  expect(screen.getByRole("button", { name: "正在验证并安装…" })).toBeDisabled();
  expect(commands).toEqual(["check_update", "install_update"]);
  await act(async () => fail("签名验证失败"));
  expect(toast.error).toHaveBeenCalledWith("签名验证失败");
  fireEvent.click(screen.getByRole("button", { name: "立即更新" }));
  await waitFor(() => expect(commands).toEqual(["check_update", "install_update", "install_update"]));
  await act(async () => fail("网络断开"));
});
