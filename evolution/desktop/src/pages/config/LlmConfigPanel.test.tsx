import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { toast } from "sonner";
import { listLlmConfigs, testLlmConfig, updateLlmConfig, type LlmConfigItem, type LlmConfigTestResult } from "@/lib/api";
import LlmConfigPanel from "./LlmConfigPanel";

vi.mock("@/lib/api", () => ({
  listLlmConfigs: vi.fn(),
  testLlmConfig: vi.fn(),
  updateLlmConfig: vi.fn(),
  createLlmConfig: vi.fn(),
  deleteLlmConfig: vi.fn(),
  activateLlmConfig: vi.fn(),
  listJudgeGroups: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn(), dismiss: vi.fn() } }));

const oldConfig: LlmConfigItem = {
  id: 1, name: "GLM", base_url: "https://provider.example/v1", model: "glm-test",
  has_key: true, key_hint: "old1", is_active: true, scope: "executor",
  created_at: "2026-11-23T00:00:00Z", updated_at: "2026-11-23T00:00:00Z",
};
const updatedConfig = { ...oldConfig, key_hint: "new2", updated_at: "2026-11-24T00:00:00Z" };
const quotaError = { ok: false, latency_ms: 12, error: "旧 Key 额度不足" };
const connected = { ok: true, latency_ms: 24, error: null };

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(listLlmConfigs).mockResolvedValueOnce([oldConfig]).mockResolvedValue([updatedConfig]);
  vi.mocked(updateLlmConfig).mockResolvedValue(updatedConfig);
});

async function saveNewKey() {
  fireEvent.click(screen.getByRole("button", { name: "编辑" }));
  fireEvent.change(screen.getByLabelText(/API Key/), { target: { value: "test-key-new2" } });
  fireEvent.click(screen.getByRole("button", { name: "保存" }));
  await screen.findByText(/••••new2/);
}

it("保存新 Key 后移除旧连通结果和提醒", async () => {
  vi.mocked(testLlmConfig).mockResolvedValueOnce(quotaError);
  render(<LlmConfigPanel scope="executor" />);
  fireEvent.click(await screen.findByRole("button", { name: "测试" }));
  await screen.findByText(/旧 Key 额度不足/);

  await saveNewKey();

  expect(updateLlmConfig).toHaveBeenCalledWith(oldConfig.id, expect.objectContaining({ api_key: "test-key-new2" }));
  expect(screen.queryByText(/旧 Key 额度不足/)).not.toBeInTheDocument();
  expect(toast.dismiss).toHaveBeenCalledWith(vi.mocked(toast.error).mock.calls[0][1]?.id);
});

it("旧 Key 的迟到结果不覆盖新 Key 的连通结果", async () => {
  let finishOldTest!: (result: LlmConfigTestResult) => void;
  const oldTest = new Promise<LlmConfigTestResult>((resolve) => { finishOldTest = resolve; });
  vi.mocked(testLlmConfig).mockReturnValueOnce(oldTest).mockResolvedValueOnce(connected);
  render(<LlmConfigPanel scope="executor" />);
  fireEvent.click(await screen.findByRole("button", { name: "测试" }));
  expect(screen.getByRole("button", { name: "测试中…" })).toBeDisabled();

  await saveNewKey();
  fireEvent.click(screen.getByRole("button", { name: "测试" }));
  await screen.findByText(/连通正常，延迟 24ms/);
  await act(async () => { finishOldTest(quotaError); });

  expect(screen.getByText(/连通正常，延迟 24ms/)).toBeInTheDocument();
  expect(screen.queryByText(/旧 Key 额度不足/)).not.toBeInTheDocument();
  expect(toast.error).not.toHaveBeenCalled();
});
