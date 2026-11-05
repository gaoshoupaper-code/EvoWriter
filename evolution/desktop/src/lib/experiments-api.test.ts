import { beforeEach, expect, it, vi } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { importExperimentPackage, restoreExperiment } from "./api";

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
