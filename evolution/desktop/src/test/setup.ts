// vitest 全局 setup（RSK-003 桌面测试基建）。
// 注册 jest-dom 断言。假时钟在各测试内按需启用（避免初始 effect 异步落定被冻住）。
import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";

// jsdom 无 Tauri 运行时：mock Tauri 前端 API（UpdateBanner 等 Shell 级组件用），
// 防未捕获拒绝污染输出、掩盖真实错误。
vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn().mockResolvedValue(() => {}),
}));

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});
