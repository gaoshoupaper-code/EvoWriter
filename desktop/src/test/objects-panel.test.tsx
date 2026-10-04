/**
 * 物品面板组件测试（REQ-20261004-221109 FR-007 / AC-008 前端半）。
 *
 * 覆盖：
 *   1. 有卡：左侧列表渲染物品名，选中项右侧渲染卡片 markdown（含轨迹表）
 *   2. 空态：无卡 workspace 显示「本作品暂无物品卡」，不报错
 *   3. 选中项保持：activeFilename 不在列表时回退第一项
 */
import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { render } from "@testing-library/react";
import { ObjectsPanel } from "@/components/workspace/ObjectsPanel";

const CARD_A = `# 青云剑

## 基本信息

- 名称：青云剑
- 类型：武器
- 叙事可见性：明线

## 轨迹

| 事件 | 变化 | 归属 | 备注 |
|------|------|------|------|
| 少年拾剑 | 登场 | 林岸 | 略 |`;

const CARD_B = CARD_A.replace("青云剑", "焚天诀").replace("武器", "功法");

function renderPanel(props: Partial<Parameters<typeof ObjectsPanel>[0]> = {}) {
  return render(
    <MemoryRouter>
      <ObjectsPanel
        objects={[]}
        activeFilename=""
        loading={false}
        onSelectObject={() => {}}
        {...props}
      />
    </MemoryRouter>,
  );
}

describe("ObjectsPanel", () => {
  it("渲染物品列表与选中卡内容", () => {
    renderPanel({
      objects: [
        { filename: "青云剑.md", name: "青云剑", markdown: CARD_A },
        { filename: "焚天诀.md", name: "焚天诀", markdown: CARD_B },
      ],
      activeFilename: "青云剑.md",
    });
    // 列表项（button 内）与卡片标题（h1）各就各位
    expect(screen.getByRole("button", { name: /青云剑/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /焚天诀/ })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "青云剑", level: 1 })).toBeTruthy();
  });

  it("点击列表项切换选中卡", async () => {
    const onSelect = vi.fn();
    renderPanel({
      objects: [
        { filename: "青云剑.md", name: "青云剑", markdown: CARD_A },
        { filename: "焚天诀.md", name: "焚天诀", markdown: CARD_B },
      ],
      activeFilename: "青云剑.md",
      onSelectObject: onSelect,
    });
    await userEvent.click(screen.getByText("焚天诀"));
    expect(onSelect).toHaveBeenCalledWith("焚天诀.md");
  });

  it("空态：无卡显示空态文案", () => {
    renderPanel();
    expect(screen.getByText("本作品暂无物品卡")).toBeTruthy();
  });

  it("activeFilename 失效时回退第一项", () => {
    renderPanel({
      objects: [{ filename: "青云剑.md", name: "青云剑", markdown: CARD_A }],
      activeFilename: "不存在的.md",
    });
    expect(screen.getByRole("heading", { name: "青云剑", level: 1 })).toBeTruthy();
  });
});
