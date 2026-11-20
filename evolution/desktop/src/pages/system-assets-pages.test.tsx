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
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  getSnapshots: vi.fn(),
  getHarnessElements: vi.fn(),
  getMemoryElements: vi.fn(),
  getUpgradeDiff: vi.fn(),
  getVersions: vi.fn(),
}));

vi.mock("@/lib/manual-assets-api", () => ({
  getManualAssetDraft: vi.fn(),
  saveManualAssetDraft: vi.fn(),
  releaseManualAssetDraft: vi.fn(),
  discardManualAssetDraft: vi.fn(),
}));

import * as apiModule from "@/lib/api";
import type { Snapshot, UpgradeDiffView } from "@/lib/api";
import * as manualModule from "@/lib/manual-assets-api";
import type { ManualAssetDraft } from "@/lib/manual-assets-api";
import HarnessPage from "@/pages/harness";
import VersionsPage from "@/pages/versions";

const api = vi.mocked(apiModule);
const manualApi = vi.mocked(manualModule);

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
  manualApi.getManualAssetDraft.mockResolvedValue({
    base_version: 14, base_commit: "c14", revision: 0, state: "editing", agents: null, changes: [],
  });
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

  // ── 主/SubAgent 高层切换（REQ-20261006-130414 FR-006/007 / AC-009）──

  const MANIFEST_ELEMENTS = {
    source_commit: "c15",
    has_source: true,
    layout: "manifest" as const,
    agents: [
      {
        name: "storybuilding", kind: "main", role: "main" as const,
        display_name: "故事专家", runtime_name: "storybuilding",
        prompt: { body: "主 agent 提示词" }, skills: [], middlewares: [], tools: [],
      },
      {
        name: "storybuilding_review", kind: "sub", role: "sub" as const,
        display_name: "故事审查", runtime_name: "review",
        prompt: { body: "审查器提示词" }, skills: [], middlewares: [], tools: [],
      },
    ],
    tools: [],
    subagent_relations: [
      { from: "storybuilding", to: "storybuilding_review", role: "故事审查" },
    ],
    unmounted: {
      subagents: ["subagents/orphan_agent.py"],
      middleware: [], prompts: [], skills: [],
    },
  };

  function mockManifest() {
    api.getSnapshots.mockResolvedValue(SNAPSHOTS);
    api.getHarnessElements.mockResolvedValue(MANIFEST_ELEMENTS);
    api.getMemoryElements.mockResolvedValue({ version: 14, has_source: true, elements: [] });
    api.getUpgradeDiff.mockResolvedValue(makeUpgradeDiff());
  }

  it("默认主Agent 视角：四 Tab 隐藏 Memory，无子代理选择器（AC-009）", async () => {
    mockManifest();
    render(<HarnessPage />);
    await screen.findByText("主Agent（故事专家）");
    expect(screen.getByText("SubAgent（1）")).toBeTruthy();
    expect(screen.queryByText("Memory")).toBeNull();
    expect(screen.queryByText("故事审查（review）")).toBeNull();
  });

  it("切到 SubAgent 视角：四 Tab 无 Memory，选择器 + 未挂载孤儿可见（AC-009）", async () => {
    mockManifest();
    render(<HarnessPage />);
    await screen.findByText("SubAgent（1）");
    fireEvent.click(screen.getByText("SubAgent（1）"));
    // 子代理选择器（含运行时委托名）与未挂载孤儿（DEC-005 诚实呈现）
    expect(await screen.findByText("故事审查（review）")).toBeTruthy();
    expect(screen.getByText("orphan_agent · 未挂载")).toBeTruthy();
    // Memory 为版本级要素：Sub 视角无此 Tab
    expect(screen.queryByText("Memory")).toBeNull();
    // 其余四 Tab 在
    expect(screen.getByText("Prompt")).toBeTruthy();
    expect(screen.getByText("Middleware")).toBeTruthy();
  });

  const basePrompt = {
    path: "prompts/main.md", title: "主角色提示词", kind: "base" as const, body: "生产提示词",
  };
  const existingDraft: ManualAssetDraft = {
    base_version: 14, base_commit: "c14", revision: 7, state: "editing",
    changes: [{ role: "storybuilding", operation: "prompt", name: "主角色提示词", before: basePrompt.body, after: "手动提示词" }],
    agents: [
      { name: "storybuilding", prompt_files: [{ ...basePrompt, body: "手动提示词" }], skills: [] },
      { name: "storybuilding_review", prompt_files: [], skills: [] },
    ],
  };

  it("恢复已存草稿，合并其他角色的导入并预览整批上线", async () => {
    mockManifest();
    manualApi.getManualAssetDraft.mockResolvedValue(existingDraft);
    const skillContent = "---\nname: editor\ndescription: 审查节奏\n---\n核对每段节奏。";
    const changed: ManualAssetDraft = {
      ...existingDraft, revision: 8,
      changes: [...existingDraft.changes, {
        role: "storybuilding_review", operation: "skill_import", name: "editor", before: null, after: skillContent,
      }],
    };
    manualApi.saveManualAssetDraft.mockResolvedValue(changed);
    const pending: ManualAssetDraft = { ...changed, state: "unconfirmed", published_version: 15 };
    manualApi.releaseManualAssetDraft.mockResolvedValue(pending);

    render(<HarnessPage />);
    expect(await screen.findByText("手动提示词")).toBeTruthy();
    fireEvent.click(screen.getByText("SubAgent（1）"));
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Skills" }), { button: 0 });
    const file = new File([skillContent], "SKILL.md");
    Object.defineProperty(file, "arrayBuffer", { value: async () => new TextEncoder().encode(skillContent).buffer });
    fireEvent.change(screen.getByLabelText("导入 SKILL.md"), { target: { files: [file] } });
    expect(await screen.findByText("2 项手动草稿待上线 · 基于 v14")).toBeTruthy();
    expect(manualApi.saveManualAssetDraft).toHaveBeenCalledWith(existingDraft, {
      role: "storybuilding_review", operation: "skill_import", filename: "SKILL.md", content: skillContent,
    });

    fireEvent.click(screen.getByRole("button", { name: "预览上线变更" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "故事专家" })).toBeTruthy();
    expect(within(dialog).getByRole("heading", { name: "故事审查" })).toBeTruthy();
    expect(within(dialog).getByText("修改提示词 · 主角色提示词")).toBeTruthy();
    expect(within(dialog).getByText("导入技能 · editor")).toBeTruthy();
    expect(manualApi.releaseManualAssetDraft).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "确认上线" }));
    expect(await screen.findByText("v15 等待执行端确认")).toBeTruthy();
    expect(manualApi.releaseManualAssetDraft).toHaveBeenCalledWith(changed);
    expect(manualApi.releaseManualAssetDraft).toHaveBeenCalledTimes(1);

    const activated: ManualAssetDraft = {
      base_version: 15, base_commit: "c15-manual", revision: 8, state: "activated",
      published_version: 15, agents: null, changes: [],
    };
    manualApi.getManualAssetDraft.mockResolvedValue(activated);
    api.getSnapshots.mockResolvedValue([
      makeSnapshot(15, "c15-manual", { status: "production" }), makeSnapshot(14, "c14", { status: "retired" }),
    ]);
    fireEvent.click(screen.getByRole("button", { name: "核对上线状态" }));
    await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("15"));
    expect(await screen.findByText("v15 已上线")).toBeTruthy();
    expect(manualApi.releaseManualAssetDraft).toHaveBeenCalledTimes(1);
    const refreshCount = api.getSnapshots.mock.calls.length;
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "14" } });
    await screen.findByText("Prompt");
    expect(screen.getByRole("combobox")).toHaveValue("14");
    expect(api.getSnapshots).toHaveBeenCalledTimes(refreshCount);
    expect(screen.queryByRole("region", { name: "手动资产草稿" })).toBeNull();
  });

  it("上线响应丢失后，核对到同一份草稿已发布并关闭确认", async () => {
    mockManifest();
    manualApi.getManualAssetDraft.mockResolvedValueOnce(existingDraft).mockResolvedValue({
      ...existingDraft, state: "unconfirmed", published_version: 15,
    });
    manualApi.releaseManualAssetDraft.mockRejectedValue(new Error("网络连接已断开"));
    render(<HarnessPage />);

    fireEvent.click(await screen.findByRole("button", { name: "预览上线变更" }));
    fireEvent.click(screen.getByRole("button", { name: "确认上线" }));

    expect(await screen.findByText("v15 等待执行端确认")).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.queryByRole("alert")).toBeNull();
    expect(manualApi.releaseManualAssetDraft).toHaveBeenCalledTimes(1);
  });

  it("其他套的路由生效后，旧页不能发送上线请求", async () => {
    const original = window.location.href;
    try {
      window.history.replaceState(null, "", "#/harness?agent=outline");
      mockManifest();
      manualApi.getManualAssetDraft.mockResolvedValue(existingDraft);
      render(<HarnessPage />);

      fireEvent.click(await screen.findByRole("button", { name: "预览上线变更" }));
      window.history.replaceState(null, "", "#/harness?agent=volume");
      fireEvent.click(screen.getByRole("button", { name: "确认上线" }));

      expect(await screen.findByRole("alert")).toHaveTextContent("已离开当前 Harness 资产页");
      expect(manualApi.releaseManualAssetDraft).not.toHaveBeenCalled();
    } finally {
      window.history.replaceState(null, "", original);
    }
  });

  it("生产冲突后保留草稿详情，历史版本只读", async () => {
    mockManifest();
    manualApi.getManualAssetDraft.mockResolvedValue(existingDraft);
    manualApi.releaseManualAssetDraft.mockRejectedValue(new Error("生产版本冲突"));
    render(<HarnessPage />);
    fireEvent.click(await screen.findByRole("button", { name: "预览上线变更" }));
    manualApi.getManualAssetDraft.mockResolvedValue({ ...existingDraft, state: "stale" });
    fireEvent.click(screen.getByRole("button", { name: "确认上线" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("生产版本冲突");
    expect(screen.getByText("修改提示词 · 主角色提示词")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "确认上线" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(screen.getByText("生产版本已变化，手动草稿已保留。")).toBeTruthy();

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "13" } });
    await screen.findByText("Prompt");
    expect(screen.queryByRole("button", { name: "编辑" })).toBeNull();
    expect(screen.queryByRole("button", { name: "查看草稿变更" })).toBeNull();
  });
});
