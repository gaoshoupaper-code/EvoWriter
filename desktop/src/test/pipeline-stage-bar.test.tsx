/**
 * PipelineStageBar 段间切换交互测试（REQ-20261019-232617 FR-004，DEC-008）。
 *
 * 已完成段可点跳段/回退：两段式确认——首点亮「回X？」，3 秒内再点才回调
 * onStageJump；active 段与未完成段不可点；ready 信号显示「已齐全」。
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PipelineStageBar } from "@/components/workspace/PipelineStageBar";
import type { PipelineStatus } from "@/lib/types";

const PIPELINE: PipelineStatus = {
  kind: "five",
  phase: "outline",
  next_unit: "总纲",
  awaiting: {},
};

function fileRelsOf(...rels: string[]): string[] {
  return rels;
}

describe("PipelineStageBar 段间切换人工闸（232617 FR-004）", () => {
  it("已完成段可点：两段式确认后才回调 onStageJump（AC-006）", () => {
    const onStageJump = vi.fn();
    render(
      <PipelineStageBar
        pipeline={PIPELINE}
        fileRels={fileRelsOf("主题.md")}
        onStageJump={onStageJump}
      />,
    );
    const jump = screen.getByRole("button", { name: /回到主题/ });
    fireEvent.click(jump);
    expect(onStageJump).not.toHaveBeenCalled(); // 首点只亮确认态
    const confirm = screen.getByRole("button", { name: /再点一次确认回到「主题」|回主题？/ });
    fireEvent.click(confirm);
    expect(onStageJump).toHaveBeenCalledWith("theme");
  });

  it("active 段与未完成段不渲染跳段按钮", () => {
    render(
      <PipelineStageBar
        pipeline={PIPELINE}
        fileRels={fileRelsOf("主题.md")}
        onStageJump={vi.fn()}
      />,
    );
    // 当前段（总纲，未完成）不是按钮
    expect(screen.queryByRole("button", { name: /回到总纲/ })).toBeNull();
    // 卷纲及以后未完成
    expect(screen.queryByRole("button", { name: /回到卷纲/ })).toBeNull();
  });

  it("无 onStageJump 时已完成段不可点", () => {
    render(<PipelineStageBar pipeline={PIPELINE} fileRels={fileRelsOf("主题.md")} />);
    expect(screen.queryByRole("button", { name: /回到主题/ })).toBeNull();
  });

  it("ready 信号显示「已齐全」提示（FR-001 展示面）", () => {
    render(
      <PipelineStageBar
        pipeline={{ ...PIPELINE, ready: { ready: true, next: "volume" } }}
        fileRels={fileRelsOf("主题.md")}
      />,
    );
    expect(screen.getByText(/已齐全，可进入下一步/)).toBeInTheDocument();
  });
});
