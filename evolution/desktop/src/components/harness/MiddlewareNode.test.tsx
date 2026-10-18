/**
 * MiddlewareNode 渲染韧性测试。
 *
 * 覆盖：
 * - mw.params 缺失（后端清单投影曾漏字段）不崩组件——渲染参数区显示「无参数」
 *   而不是 Object.keys(undefined) 抛 TypeError 白屏
 */
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MiddlewareNode } from "./MiddlewareNode";

const BASE_MW = {
  hooks: ["before_agent"],
  group: "base",
  class_name: "ErrorRecoveryMiddleware",
  source_path: "middleware/error_recovery.py",
  description: "基础链。",
  optional: false,
};

/** 打开节点 → 参数区在 Sheet 弹层内，需先点开才渲染 */
function renderOpened(mw: unknown) {
  // @ts-expect-error 测试数据故意宽松——覆盖 params 缺失的破损契约形态
  render(<MiddlewareNode mw={mw} activeHook="before_agent" />);
  fireEvent.click(screen.getByText("ErrorRecoveryMiddleware"));
}

describe("MiddlewareNode 渲染韧性", () => {
  it("mw.params 缺失时不崩，参数区显示无参数", () => {
    // params 故意缺失——模拟后端清单投影曾漏字段的历史数据
    renderOpened({ ...BASE_MW });

    expect(screen.getByText("无参数。")).toBeTruthy();
  });

  it("mw.params 正常提供时照常展示", () => {
    renderOpened({ ...BASE_MW, params: { max_retries: 3 } });

    expect(screen.getByText(/"max_retries": 3/)).toBeTruthy();
  });
});
