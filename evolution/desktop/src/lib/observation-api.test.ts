import { beforeEach, expect, it, vi } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { getActiveRuns, getTraces, getWorkloadProfiles } from "./api";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
beforeEach(() => { window.location.hash = "#/agents/chapter/traces"; vi.mocked(invoke).mockReset().mockResolvedValue({ status: 200, headers: {}, body: "[]" }); });

it("用户历史与分析请求显式携带用户范围", async () => {
  await getTraces();
  await getWorkloadProfiles(168);
  const paths = vi.mocked(invoke).mock.calls.map(([, value]) => (value as { request: { path: string } }).request.path);
  expect(paths).toEqual(["/evolution-api/chapter/api/traces?observation_scope=user", "/evolution-api/chapter/api/analysis/profiles?hours=168&observation_scope=user"]);
});

it("实验活跃查询通过中继保留实验范围，不回退为用户", async () => {
  await getActiveRuns("experiment");
  expect(invoke).toHaveBeenCalledWith("http_request", { request: {
    path: "/evolution-api/chapter/api/active-runs?observation_scope=experiment", method: "GET", headers: null, body: null, stream: false,
  } });
});
