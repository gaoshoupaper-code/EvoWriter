/**
 * 系统资产两页组件测试（REQ-20260923-145931）。
 *
 * 覆盖 AC 的可自动化部分：
 * - AC-001 账本列表渲染 + 发版数摘要（9 次发版 · 6 份不同代码）
 * - AC-002 同代码标注（v9/v11 → 代码同 v8，v12 → 代码同 v10）
 * - AC-003 代码基于解析（v13 基于 v14 倒挂 / v6 初始版本）
 * - AC-004 升级总览（ancestor 摘要 / same_code 无差异占位）
 * - AC-007 Platform 不可达报错态 + 重试（DEC-005）
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  getSnapshots: vi.fn(),
  getHarnessElements: vi.fn(),
  getMemoryElements: vi.fn(),
  getUpgradeDiff: vi.fn(),
  getVersions: vi.fn(),
}));

import * as apiModule from "@/lib/api";
import type { Snapshot, UpgradeDiffView } from "@/lib/api";
import HarnessPage from "@/pages/harness";
import VersionsPage from "@/pages/versions";

const api = vi.mocked(apiModule);

// ── 造数：对齐线上账本 v6–v14 关键形态（9 条流水 / 6 份不同代码） ──

function makeSnapshot(
  version: number,
  commit: string,
  overrides: Partial<Snapshot> = {},
): Snapshot {
  return {
    version,
    status: version === 14 ? "production" : "retired",
    change_summary: `v${version} 说明`,
    created_at: "2026-09-22T00:00:00",
    commit,
    same_code_as: null,
    based_on: null,
    based_on_status: "root",
    ...overrides,
  };
}

const SNAPSHOTS: Snapshot[] = [
  makeSnapshot(14, "c14", { based_on: 10, based_on_status: "resolved" }),
  makeSnapshot(13, "c13", { based_on: 14, based_on_status: "resolved" }),
  makeSnapshot(12, "c10", { same_code_as: 10 }),
  makeSnapshot(11, "c8", { same_code_as: 8 }),
  makeSnapshot(10, "c10", { based_on: 8, based_on_status: "resolved" }),
  makeSnapshot(9, "c8", { same_code_as: 8 }),
  makeSnapshot(8, "c8", { based_on: 7, based_on_status: "resolved" }),
  makeSnapshot(7, "c7", { based_on: 6, based_on_status: "resolved" }),
  makeSnapshot(6, "c6"),
];

function makeUpgradeDiff(overrides: Partial<UpgradeDiffView> = {}): UpgradeDiffView {
  return {
    version: 14,
    target_commit: "c14",
    base_kind: "ancestor",
    base_version: 10,
    base_commit: "c10",
    same_code_as: null,
    changes: {
      agents: [
        {
          agent: "storybuilding",
          diff: {
            prompt: { hunks: [], summary: { added: 12, removed: 3 } },
            skills: { added: ["skills/storybuilding/new"], removed: [], unchanged_count: 4 },
            processors: [
              {
                key: { hook: "before_model", group: "agent" },
                change_type: "modified",
                class_change: { old: "PacingMiddleware", new: "PacingMiddleware" },
                params_change: { old: { max: 5 }, new: { max: 10 } },
              },
            ],
          },
        },
      ],
      intent: null,
    },
    ...overrides,
  };
}

const ELEMENTS = {
  source_commit: "c14",
  has_source: true,
  agents: [],
  tools: [],
  subagent_relations: [],
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("VersionsPage（账本时间线）", () => {
  function mockOk() {
    api.getVersions.mockResolvedValue({
      items: SNAPSHOTS,
      total: 9,
      production_version: 14,
      limit: 200,
      offset: 0,
    });
  }

  it("渲染全部账本条目 + 发版数摘要（AC-001）", async () => {
    mockOk();
    render(<VersionsPage />);
    // v14 同时出现在时间线与默认选中的详情区，取多处匹配
    await screen.findAllByText("v14");
    expect(screen.getByText(/9 次发版/)).toBeTruthy();
    expect(screen.getByText(/6 份不同代码/)).toBeTruthy();
    expect(screen.getByText(/当前生产 v14/)).toBeTruthy();
    // 同代码标注（AC-002）
    expect(screen.getAllByText("代码同 v8").length).toBe(2); // v9 + v11
    expect(screen.getByText("代码同 v10")).toBeTruthy();
    // 持有者自身无标注
    expect(screen.queryByText("代码同 v14")).toBeNull();
  });

  it("选中 v13 显示代码基于 v14（倒挂场景），v6 显示初始版本（AC-003）", async () => {
    mockOk();
    render(<VersionsPage />);
    await screen.findByText("v13");
    fireEvent.click(screen.getByText("v13"));
    expect(screen.getByText("代码基于：v14")).toBeTruthy();
    fireEvent.click(screen.getByText("v6"));
    expect(screen.getByText("初始版本")).toBeTruthy();
  });

  it("Platform 不可达显示报错态 + 重试，不渲染版本列表（AC-007）", async () => {
    api.getVersions.mockRejectedValue(new Error("Platform 账本不可达：boom"));
    render(<VersionsPage />);
    await screen.findByText(/Platform 账本不可达/);
    expect(screen.getByText("重试")).toBeTruthy();
    expect(screen.queryByText("v14")).toBeNull();
  });

  it("错误消息不双前缀：detail 已含分类前缀时原样渲染一次", async () => {
    api.getVersions.mockRejectedValue(new Error("Platform 账本不可达：boom"));
    render(<VersionsPage />);
    // 修复前会渲染成「Platform 账本不可达：Platform 账本不可达：boom」
    await screen.findByText("Platform 账本不可达：boom");
    expect(screen.queryByText(/Platform 账本不可达：Platform/)).toBeNull();
  });
});

describe("HarnessPage（要素透视）", () => {
  function mockOk(diff: UpgradeDiffView = makeUpgradeDiff()) {
    api.getSnapshots.mockResolvedValue(SNAPSHOTS);
    api.getHarnessElements.mockResolvedValue(ELEMENTS);
    api.getMemoryElements.mockResolvedValue({ version: 14, has_source: true, elements: [] });
    api.getUpgradeDiff.mockResolvedValue(diff);
  }

  it("渲染升级总览摘要：相对 v10 + 要素变更行（AC-004）", async () => {
    mockOk();
    render(<HarnessPage />);
    await screen.findByText("Prompt");
    expect(screen.getByText("相对 v10")).toBeTruthy();
    expect(screen.getByText(/prompt \+12\/-3 行/)).toBeTruthy();
    expect(screen.getByText(/skills \+1\/-0/)).toBeTruthy();
    expect(screen.getByText(/middleware 1 修改/)).toBeTruthy();
  });

  it("同代码版本显示无差异占位（AC-004）", async () => {
    mockOk(makeUpgradeDiff({
      version: 9, target_commit: "c8", base_kind: "same_code",
      base_version: null, base_commit: null, same_code_as: 8,
      changes: { agents: [], intent: null },
    }));
    render(<HarnessPage />);
    await screen.findByText(/代码同 v8，与基线无差异/);
  });

  it("diff 拉取失败显示变更计算失败占位，不停留假加载（FR-003）", async () => {
    api.getSnapshots.mockResolvedValue(SNAPSHOTS);
    api.getHarnessElements.mockResolvedValue(ELEMENTS);
    api.getMemoryElements.mockResolvedValue({ version: 14, has_source: true, elements: [] });
    api.getUpgradeDiff.mockRejectedValue(new Error("HTTP 500"));
    render(<HarnessPage />);
    await screen.findByText(/变更计算失败/);
    expect(screen.queryByText(/计算升级差异/)).toBeNull();
    // 要素展示不受阻断
    await screen.findByText("Prompt");
  });

  it("Platform 不可达显示报错态 + 重试，不渲染版本选择（AC-007）", async () => {
    api.getSnapshots.mockRejectedValue(new Error("Platform 账本不可达：boom"));
    render(<HarnessPage />);
    await screen.findByText(/Platform 账本不可达/);
    expect(screen.getByText("重试")).toBeTruthy();
    expect(screen.queryByText("选择版本：")).toBeNull();
  });

  it("重试成功后恢复版本列表（AC-007）", async () => {
    api.getSnapshots.mockRejectedValueOnce(new Error("Platform 账本不可达：boom"));
    render(<HarnessPage />);
    await screen.findByText("重试");
    mockOk();
    fireEvent.click(screen.getByText("重试"));
    await screen.findByText("选择版本：");
    // 下拉含同代码标注（AC-002）
    const option = screen.getByRole("option", { name: /v9.*代码同 v8/ }) as HTMLOptionElement;
    expect(option).toBeTruthy();
  });
});
