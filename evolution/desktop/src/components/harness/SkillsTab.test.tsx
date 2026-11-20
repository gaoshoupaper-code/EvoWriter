/**
 * SkillsTab 技能卡片渲染测试。
 *
 * 覆盖：
 * - SKILL.md 正文（剥 frontmatter 后）默认以 markdown 渲染可见
 * - 头部展示技能名 + 路径 + frontmatter 描述
 * - 点卡片头可收起正文；收起后正文不可见
 * - diff 模式：新增技能卡片头标绿、删除路径红底行从 diff 补出
 * - content 为空（读取失败）显示 load_error，不渲染正文
 */
import { act, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { HarnessElementView, AgentDiff } from "@/lib/api";
import { SkillsTab } from "./SkillsTab";

const SKILL_MD = [
  "---",
  "name: storybuilding-initial",
  "description: 首次故事骨架构建。",
  "---",
  "",
  "# storybuilding-initial",
  "",
  "首次故事骨架构建流程：",
  "",
  "1. 读取 `demand.md`",
  "2. 产出骨架",
  "",
  "## 注意事项",
  "- 只生成主线",
].join("\n");

function makeAgent(overrides: Partial<HarnessElementView> = {}): HarnessElementView {
  return {
    name: "storybuilding",
    kind: "main",
    role: "main",
    prompt: { body: "" },
    skills: [
      {
        path: "skills/storybuilding-initial",
        name: "storybuilding-initial",
        description: "首次故事骨架构建。",
        content: SKILL_MD,
        load_error: null,
      },
    ],
    middlewares: [],
    ...overrides,
  };
}

describe("SkillsTab 技能卡片", () => {
  it("默认渲染 SKILL.md 正文（剥 frontmatter）为 markdown", () => {
    render(<SkillsTab agents={[makeAgent()]} diffs={null} />);

    // 正文标题渲染为 h1，frontmatter 的 name/description 不混进正文
    expect(screen.getByRole("heading", { level: 1, name: "storybuilding-initial" })).toBeTruthy();
    expect(screen.getByText("只生成主线")).toBeTruthy();
    // frontmatter 块本身不出现在文档区（name 字段以字段名重复出现在卡片头）
    expect(screen.queryByText(/^name: storybuilding-initial$/)).toBeNull();
  });

  it("卡片头展示技能名 + 路径 + 描述；点击收起/展开正文", () => {
    render(<SkillsTab agents={[makeAgent()]} diffs={null} />);

    expect(screen.getByText("skills/storybuilding-initial")).toBeTruthy();
    expect(screen.getByText("首次故事骨架构建。")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /storybuilding-initial/ }));
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /storybuilding-initial/ }));
    expect(screen.getByRole("heading", { level: 1 })).toBeTruthy();
  });

  it("diff 模式：新增技能头标绿、删除路径红底行补出", () => {
    const diffs = new Map<string, AgentDiff>([
      [
        "storybuilding",
        {
          prompt: null,
          skills: { added: ["skills/storybuilding-initial"], removed: ["skills/storybuilding/old"], unchanged_count: 0 },
          processors: [],
        },
      ],
    ]);
    const { container } = render(<SkillsTab agents={[makeAgent()]} diffs={diffs} />);

    fireEvent.click(screen.getByRole("switch"));
    expect(screen.getByText("skills/storybuilding/old")).toBeTruthy();
    expect(container.querySelector(".skill-card.diff-add")).toBeTruthy();
    expect(container.querySelector(".skill-row.diff-del")).toBeTruthy();
  });

  it("content 为空时显示读取错误，不渲染正文", () => {
    const agent = makeAgent({
      skills: [
        {
          path: "skills/broken",
          name: "broken",
          description: null,
          content: null,
          load_error: "git show 失败",
        },
      ],
    });
    render(<SkillsTab agents={[agent]} diffs={null} />);

    expect(screen.getByText(/git show 失败/)).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
  });

  it("所有 agent 无技能时显示空态提示，不渲染空白页", () => {
    const bare = makeAgent({ skills: [] });
    render(<SkillsTab agents={[bare]} diffs={null} />);

    // 空态文案可见（v48 起架构无 skills 目录，诚实提示避免误判为加载失败）
    expect(screen.getByText("该版本所有 Agent 均无 skills。")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /skill-card/ })).toBeNull();
  });

  it("只接受 SKILL.md，导入空技能页所选角色", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<SkillsTab agents={[makeAgent({ name: "review", skills: [] })]} diffs={null} onSave={onSave} />);
    const input = screen.getByLabelText("导入 SKILL.md");
    fireEvent.change(input, { target: { files: [new File(["# other"], "other.md")] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("只支持导入 SKILL.md");
    expect(onSave).not.toHaveBeenCalled();

    const file = new File([SKILL_MD], "SKILL.md");
    Object.defineProperty(file, "arrayBuffer", { value: async () => new TextEncoder().encode(SKILL_MD).buffer });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(onSave).toHaveBeenCalledWith({
      role: "review", operation: "skill_import", filename: "SKILL.md", content: SKILL_MD,
    }));
  });

  it("切出当前角色后不会提交仍在读取的技能文件", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const { unmount } = render(<SkillsTab agents={[makeAgent({ name: "review" })]} diffs={null} onSave={onSave} />);
    let finishRead!: (buffer: ArrayBuffer) => void;
    const file = new File([SKILL_MD], "SKILL.md");
    Object.defineProperty(file, "arrayBuffer", { value: () => new Promise<ArrayBuffer>((resolve) => { finishRead = resolve; }) });

    fireEvent.change(screen.getByLabelText("导入 SKILL.md"), { target: { files: [file] } });
    unmount();
    await act(async () => { finishRead(new TextEncoder().encode(SKILL_MD).buffer); });

    expect(onSave).not.toHaveBeenCalled();
  });

  it("编辑和移除只发送当前角色与选中的技能路径", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<SkillsTab agents={[makeAgent({ name: "review" })]} diffs={null} onSave={onSave} />);
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    expect(screen.getByRole("textbox")).toHaveValue(SKILL_MD);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: SKILL_MD + "\n改后内容" } });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith({
      role: "review", operation: "skill_edit", path: "skills/storybuilding-initial", content: SKILL_MD + "\n改后内容",
    }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(onSave).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith({
      role: "review", operation: "skill_delete", path: "skills/storybuilding-initial",
    }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
