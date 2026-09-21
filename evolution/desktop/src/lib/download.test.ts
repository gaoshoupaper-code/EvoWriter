import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { downloadTextFile } from "@/lib/download";

/** 导出下载工具（REQ-20260921-135543 FR-009/AC-017 的可自动化部分）。 */
describe("downloadTextFile", () => {
  beforeEach(() => {
    // jsdom 不实现 createObjectURL，直接注入 mock 属性
    Object.defineProperty(URL, "createObjectURL", {
      value: vi.fn().mockReturnValue("blob:test"),
      writable: true,
      configurable: true,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      value: vi.fn(),
      writable: true,
      configurable: true,
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });
  afterEach(() => vi.restoreAllMocks());

  it("创建 Blob、触发锚点下载并回收 URL", () => {
    downloadTextFile("storyline.md", "# 大纲");
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    const blob = (URL.createObjectURL as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0] as Blob;
    expect(blob.type).toContain("text/markdown");
    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:test");
  });
});
