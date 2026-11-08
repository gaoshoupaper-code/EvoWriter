import { beforeEach, expect, it, vi } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { getExperimentEvaluationUsage, importExperimentPackage, listExperiments, restoreExperiment } from "./api";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
beforeEach(() => { vi.mocked(invoke).mockReset(); });

it("资料包字段缺失显示字段和原因，不回显请求正文", async () => {
  vi.mocked(invoke).mockResolvedValue({ status: 422, headers: {}, body: JSON.stringify({ detail: [
    { loc: ["body", "input", "confirmation_rules"], msg: "Field required", type: "missing", input: { demand_md: "不应回显的文稿" } },
  ] }) });
  const error = await importExperimentPackage({} as never).catch((cause: Error) => cause);
  expect(error).toBeInstanceOf(Error);
  expect((error as Error).message).toContain("input.confirmation_rules");
  expect((error as Error).message).toContain("缺少必填项");
  expect((error as Error).message).not.toContain("不应回显的文稿");
});

it("恢复旧版使用冻结的restore接口与同一JSON中继", async () => {
  vi.mocked(invoke).mockResolvedValue({ status: 200, headers: {}, body: "{}" });
  await restoreExperiment("experiment-1");
  expect(invoke).toHaveBeenCalledWith("http_request", { request: {
    path: "/api/experiments/runs/experiment-1/restore", method: "POST", headers: null, body: null, stream: false,
  } });
});

it("活跃实验由服务端筛选，默认仍读取全部摘要", async () => {
  vi.mocked(invoke).mockResolvedValue({ status: 200, headers: {}, body: '{"items":[]}' });
  await listExperiments();
  await listExperiments(true);
  expect(vi.mocked(invoke).mock.calls.map((call) => (call[1] as { request: { path: string } }).request.path))
    .toEqual(["/api/experiments/runs", "/api/experiments/runs?active=true"]);
});

it("评分调用明细按实验编号与当前页请求", async () => {
  vi.mocked(invoke).mockResolvedValue({ status: 200, headers: {}, body: '{"items":[],"total":0,"offset":50,"limit":50}' });
  const response = await getExperimentEvaluationUsage("experiment/1", { offset: 50, limit: 50 });
  expect(response).toEqual({ items: [], total: 0, offset: 50, limit: 50 });
  expect(invoke).toHaveBeenCalledWith("http_request", { request: {
    path: "/api/experiments/runs/experiment%2F1/usage/evaluation?offset=50&limit=50",
    method: "GET", headers: null, body: null, stream: false,
  } });
});
