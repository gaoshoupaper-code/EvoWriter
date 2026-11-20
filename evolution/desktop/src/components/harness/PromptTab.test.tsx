/**
 * PromptTab 提示词文件卡 + 草稿编辑/发版测试。
 *
 * 覆盖：
 * - 清单版本：常驻骨架 + 段规程卡片全渲染（标题/类型标/路径/全文）
 * - 旧版本（无 prompt_files）：回退单 body 全文，不拉草稿态
 * - 编辑流：点「编辑」打开草稿表单，按角色保存，失败时保留输入
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { HarnessElementView } from "@/lib/api";
import { PromptTab } from "./PromptTab";

function makeAgent(overrides: Partial<HarnessElementView> = {}): HarnessElementView {
  return {
    name: "review",
    kind: "main",
    role: "main",
    prompt: { body: "# 版本快照骨架\n快照正文" },
    prompt_files: [
      {
        path: "prompts/author_base.md",
        kind: "base",
        title: "网文创作陪练 · 常驻骨架",
        body: "# 版本快照骨架\n快照正文",
      },
      {
        path: "prompts/stage_theme.md",
        kind: "stage",
        title: "当前段规程 · 题材（theme）",
        body: "# 当前段规程 · 题材（theme）\n题材规程正文",
      },
    ],
    skills: [],
    middlewares: [],
    ...overrides,
  };
}

describe("PromptTab 提示词文件卡", () => {
  it("清单版本：骨架 + 段规程卡片全渲染", () => {
    render(<PromptTab agents={[makeAgent()]} diffs={null} />);

    expect(screen.getByText("网文创作陪练 · 常驻骨架")).toBeTruthy();
    expect(screen.getByText("当前段规程 · 题材（theme）")).toBeTruthy();
    expect(screen.getByText("常驻骨架")).toBeTruthy();
    expect(screen.getByText("段规程")).toBeTruthy();
    expect(screen.getByText(/题材规程正文/)).toBeTruthy();
  });

  it("旧版本：无 prompt_files 回退单 body，历史版只读", () => {
    const legacy = makeAgent({ name: "storybuilding", prompt_files: undefined });
    render(<PromptTab agents={[legacy]} diffs={null} />);

    expect(screen.getByText(/快照正文/)).toBeTruthy();
    expect(screen.queryByText("段规程")).toBeNull();
    expect(screen.queryByRole("button", { name: "编辑" })).toBeNull();
  });

  it("载入已保存草稿，向所选角色保存修改", async () => {
    const agent = makeAgent();
    agent.prompt_files![0].body = "# 草稿版骨架\n草稿正文";
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PromptTab agents={[agent]} diffs={null} onSave={onSave} />);

    // base + stage 两张卡各有一个编辑按钮，取第一个（author_base）
    fireEvent.click(screen.getAllByRole("button", { name: "编辑" })[0]);
    const textarea = await waitFor(() => {
      const el = screen.getByRole("textbox");
      expect((el as HTMLTextAreaElement).value).toContain("草稿正文");
      return el as HTMLTextAreaElement;
    });

    fireEvent.change(textarea, { target: { value: "# 新草稿\n改后内容" } });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));

    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith({
        role: "review", operation: "prompt", path: "prompts/author_base.md",
        content: "# 新草稿\n改后内容",
      }),
    );
  });

  it("保存失败时显示原因并保留编辑内容", async () => {
    const onSave = vi.fn().mockRejectedValue(new Error("生产版本已变化"));
    render(<PromptTab agents={[makeAgent()]} diffs={null} onSave={onSave} />);
    fireEvent.click(screen.getAllByRole("button", { name: "编辑" })[0]);
    const input = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "改后内容" } });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("生产版本已变化");
    expect(input.value).toBe("改后内容");
  });
});
