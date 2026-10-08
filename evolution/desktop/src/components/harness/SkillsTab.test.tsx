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
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it } from "vitest";
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
});
