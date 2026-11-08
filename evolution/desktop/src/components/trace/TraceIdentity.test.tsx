import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TraceIdentity, TraceIdentifier } from "./TraceIdentity";

describe("运行名称与编号（AC-003、AC-004）", () => {
  it("主标题和辅助行分开，完整标题可查看", () => {
    render(<TraceIdentity run={{ started_at: "2026-11-08T15:30:12Z",
      display_context: { workspace_title: "雾城", task_phase: "writing" } }} />);
    expect(screen.getByText("雾城 · 正文创作")).toHaveAttribute("title", "雾城 · 正文创作");
    expect(document.querySelector(".trace-identity-subtitle")).toHaveTextContent(/\d{2}:\d{2}:12/);
  });
  it("编号默认折叠，展开后复制完整原值", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    render(<TraceIdentifier traceId="trace-7bf394-complete" />);
    expect(document.querySelector("details")).not.toHaveAttribute("open");
    fireEvent.click(screen.getByText("运行标识"));
    fireEvent.click(screen.getByRole("button", { name: "复制运行编号" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("trace-7bf394-complete"));
    expect(await screen.findByText("已复制")).toBeInTheDocument();
  });
  it("剪贴板失败仍保留可选中完整编号", async () => {
    Object.defineProperty(navigator, "clipboard", { configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) } });
    render(<TraceIdentifier traceId="trace-available-for-selection" />);
    fireEvent.click(screen.getByText("运行标识"));
    fireEvent.click(screen.getByRole("button", { name: "复制运行编号" }));
    expect(await screen.findByRole("status")).toHaveTextContent("请选中编号手动复制");
    expect(screen.getByText("trace-available-for-selection")).toBeInTheDocument();
  });
});
