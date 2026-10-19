/**
 * PromptTab 提示词文件卡 + 草稿编辑/发版测试。
 *
 * 覆盖：
 * - 清单版本：常驻骨架 + 段规程卡片全渲染（标题/类型标/路径/全文）
 * - 旧版本（无 prompt_files）：回退单 body 全文，不拉草稿态
 * - 编辑流：点「编辑」打开草稿 Sheet 载入工作区内容 → 保存调 savePromptDraft
 * - 发版栏：脏时按钮可用，点击调 releasePromptDraft 并回调新版本号
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { HarnessElementView } from "@/lib/api";
import { PromptTab } from "./PromptTab";

vi.mock("@/lib/api", () => ({
  getPromptDrafts: vi.fn(),
  savePromptDraft: vi.fn(),
  releasePromptDraft: vi.fn(),
}));

import * as apiModule from "@/lib/api";

const api = vi.mocked(apiModule);
const draftApi = api as unknown as {
  getPromptDrafts: ReturnType<typeof vi.fn>;
  savePromptDraft: ReturnType<typeof vi.fn>;
  releasePromptDraft: ReturnType<typeof vi.fn>;
};

const DRAFT = {
  files: [
    {
      path: "prompts/author_base.md",
      title: "网文创作陪练 · 常驻骨架",
      body: "# 草稿版骨架\n草稿正文",
    },
  ],
  dirty: false,
  head: "0cbc5b66ffeeddccaaff00112233445566778899",
};

function makeAgent(overrides: Partial<HarnessElementView> = {}): HarnessElementView {
  return {
    name: "author",
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

beforeEach(() => {
  vi.clearAllMocks();
  draftApi.getPromptDrafts.mockResolvedValue(DRAFT);
});

describe("PromptTab 提示词文件卡", () => {
  it("清单版本：骨架 + 段规程卡片全渲染，拉取草稿态", async () => {
    render(<PromptTab agents={[makeAgent()]} diffs={null} />);

    expect(screen.getByText("网文创作陪练 · 常驻骨架")).toBeTruthy();
    expect(screen.getByText("当前段规程 · 题材（theme）")).toBeTruthy();
    expect(screen.getByText("常驻骨架")).toBeTruthy();
    expect(screen.getByText("段规程")).toBeTruthy();
    expect(screen.getByText(/题材规程正文/)).toBeTruthy();
    await waitFor(() => expect(draftApi.getPromptDrafts).toHaveBeenCalled());
  });

  it("旧版本：无 prompt_files 回退单 body，不拉草稿态", () => {
    const legacy = makeAgent({ name: "storybuilding", prompt_files: undefined });
    render(<PromptTab agents={[legacy]} diffs={null} />);

    expect(screen.getByText(/快照正文/)).toBeTruthy();
    expect(screen.queryByText("段规程")).toBeNull();
    expect(draftApi.getPromptDrafts).not.toHaveBeenCalled();
  });

  it("编辑流：Sheet 载入草稿内容，保存调 savePromptDraft", async () => {
    draftApi.savePromptDraft.mockResolvedValue({
      ok: true, path: "prompts/author_base.md", dirty: true,
    });
    render(<PromptTab agents={[makeAgent()]} diffs={null} />);

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
      expect(draftApi.savePromptDraft).toHaveBeenCalledWith(
        "prompts/author_base.md",
        "# 新草稿\n改后内容",
      ));
  });

  it("发版栏：干净时禁用；脏时可点，发版并回调新版本号", async () => {
    draftApi.releasePromptDraft.mockResolvedValue({ version: 52, commit: "abc" });
    const onReleased = vi.fn();

    // 第一遍：干净草稿 → 按钮禁用
    const { unmount } = render(
      <PromptTab agents={[makeAgent()]} diffs={null} onReleased={onReleased} />,
    );
    const cleanBtn = (await screen.findByRole("button", { name: "发版上线" })) as HTMLButtonElement;
    expect(cleanBtn.disabled).toBe(true);
    unmount();

    // 第二遍：脏草稿 → 可点，发版回调新版本号（脏态由保存后的刷新带入，非 rerender）
    draftApi.getPromptDrafts.mockResolvedValue({ ...DRAFT, dirty: true });
    render(<PromptTab agents={[makeAgent()]} diffs={null} onReleased={onReleased} />);
    const dirtyBtn = await waitFor(() => {
      const btn = screen.getByRole("button", { name: "发版上线" }) as HTMLButtonElement;
      expect(btn.disabled).toBe(false);
      return btn;
    });
    fireEvent.click(dirtyBtn);

    await waitFor(() => expect(onReleased).toHaveBeenCalledWith(52));
  });
});
