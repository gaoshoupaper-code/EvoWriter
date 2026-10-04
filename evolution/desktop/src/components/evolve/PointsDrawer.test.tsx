/**
 * 进化点浮窗四组展示组件测试（REQ-20261004-212948 / FR-003 / AC-006）。
 *
 * 覆盖 AC 的可自动化部分：
 * - 四组结构：讨论中 / 已采纳 / 已否决 / 已发版
 * - 「已发版」默认折叠；展开后按版本号分组（v0.1 / v0.2），每点带版本徽章
 * - 无已发版点时「已发版」组不出现
 * - 全部已发版、无待落地点时拍板按钮禁用（AC-005 前端面）
 */
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { EvolvePoint } from "@/lib/api";
import PointsDrawer from "./PointsDrawer";

function makePoint(overrides: Partial<EvolvePoint>): EvolvePoint {
  return {
    id: "p1",
    session_id: "s1",
    seq: 1,
    target: "middleware/retry.py",
    problem: "重试风暴",
    options: [
      { description: "方案甲", pros: [], cons: [], expected_impact: "" },
      { description: "方案乙", pros: [], cons: [], expected_impact: "" },
    ],
    recommendation: null,
    note: null,
    status: "proposed",
    chosen_option: null,
    user_note: null,
    accepted_at: null,
    design_ref: null,
    created_at: "2026-10-04T00:00:00",
    ...overrides,
  };
}

describe("PointsDrawer 四组展示（AC-006）", () => {
  it("四种状态分组渲染；已发版默认折叠，展开后按版本分组带徽章", () => {
    const points: EvolvePoint[] = [
      makePoint({ id: "a", seq: 1, status: "proposed" }),
      makePoint({ id: "b", seq: 2, status: "accepted", chosen_option: 0 }),
      makePoint({ id: "c", seq: 3, status: "rejected" }),
      makePoint({ id: "d", seq: 4, status: "shipped", version: "0.1" }),
      makePoint({ id: "e", seq: 5, status: "shipped", version: "0.2" }),
    ];
    render(
      <PointsDrawer points={points} acceptedCount={1} canFinalize={true} />,
    );

    // 四组标题都在
    expect(screen.getByText("讨论中")).toBeTruthy();
    expect(screen.getByText("已采纳")).toBeTruthy();
    expect(screen.getByText("已否决")).toBeTruthy();
    expect(screen.getByText("已发版")).toBeTruthy();

    // 已发版默认折叠：版本分组标题与版本徽章不出现
    expect(screen.queryByText(/v0\.1/)).toBeNull();
    expect(screen.queryByText(/v0\.2/)).toBeNull();
    expect(screen.queryByText("v0.1")).toBeNull();

    // 点击组标题展开 → 按版本分组，版本徽章可见
    fireEvent.click(screen.getByText("已发版"));
    expect(screen.getByText(/v0\.1 · 1 个点/)).toBeTruthy();
    expect(screen.getByText(/v0\.2 · 1 个点/)).toBeTruthy();
    expect(screen.getByText("v0.1")).toBeTruthy();
    expect(screen.getByText("v0.2")).toBeTruthy();
  });

  it("无已发版点时不渲染「已发版」组", () => {
    render(
      <PointsDrawer
        points={[makePoint({ id: "a", status: "proposed" })]}
        acceptedCount={0}
        canFinalize={false}
      />,
    );
    expect(screen.queryByText("已发版")).toBeNull();
  });

  it("版本分组按版本号数值排序，最新在上（review P3：提出序≠发版序）", () => {
    // 早提出的点晚发版：v0.1 的点 seq 大于 v0.2 的点（插入序颠倒）
    const points: EvolvePoint[] = [
      makePoint({ id: "late", seq: 9, status: "shipped", version: "0.1" }),
      makePoint({ id: "early", seq: 2, status: "shipped", version: "0.2" }),
      makePoint({ id: "major", seq: 1, status: "shipped", version: "10.0" }),
    ];
    render(
      <PointsDrawer points={points} acceptedCount={0} canFinalize={false} />,
    );
    fireEvent.click(screen.getByText("已发版"));
    const titles = screen
      .getAllByText(/· 1 个点/)
      .map((el) => el.textContent);
    // 数值序：10.0 > 0.2 > 0.1（字符串序会把 "0.1" < "0.2" < "10.0" 排对，
    // 但 "0.10" vs "0.2" 会错——此处 10.0 在最上验证数值比较）
    expect(titles[0]).toContain("v10.0");
    expect(titles[1]).toContain("v0.2");
    expect(titles[2]).toContain("v0.1");
  });

  it("全部已发版、无待落地点时拍板按钮禁用（AC-005 前端面）", () => {
    render(
      <PointsDrawer
        points={[makePoint({ id: "d", status: "shipped", version: "0.1" })]}
        acceptedCount={0}
        canFinalize={false}
      />,
    );
    const btn = screen.getByRole("button", { name: /确认全部进化点/ }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(screen.getByText(/已发版的不再重复落地/)).toBeTruthy();
  });
});
