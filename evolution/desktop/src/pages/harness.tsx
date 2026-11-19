import { useEffect, useState, useCallback } from "react";
import { toast } from "sonner";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  getSnapshots,
  getHarnessElements,
  getUpgradeDiff,
  type Snapshot,
  type HarnessElementsView,
  type UpgradeDiffView,
  type AgentDiff,
} from "@/lib/api";
import { UpgradeOverview } from "@/components/harness/UpgradeOverview";
import { PromptTab } from "@/components/harness/PromptTab";
import { SkillsTab } from "@/components/harness/SkillsTab";
import { ToolsTab } from "@/components/harness/ToolsTab";
import { CandidateEditor } from "@/components/harness/CandidateEditor";
import { MiddlewareTab } from "@/components/harness/MiddlewareTab";

/**
 * Harness 要素透视页（数据源：Platform 账本 / 架构清单，REQ-20261006-130414）。
 *
 * 当前版本的主 Agent 和助手均显示 Prompt、Skills、Tools、Middleware。
 *
 * 主/Sub 结构来自架构清单（DEC-003/004：两层切换 + 视图内子代理选择器）；
 * 清单外孤儿文件按 DEC-005 显示「未挂载」，不隐藏。
 * 旧版本（无清单）由后端回退静态布局探测，前端结构不变（FR-009）。
 * 版本要素读取 getHarnessElements，升级差异读取 getUpgradeDiff。
 *
 * 账本不可达（DEC-005）：整页报错态 + 重试，不渲染版本下拉——
 * 宁缺勿错，不回退冻结 registry 旧数据。
 */
export default function HarnessPage() {
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [elements, setElements] = useState<HarnessElementsView | null>(null);
  const [upgradeDiff, setUpgradeDiff] = useState<UpgradeDiffView | null>(null);
  const [diffFailed, setDiffFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  // 主/Sub 高层切换（DEC-003）+ SubAgent 视图内子代理选择器（DEC-004）
  const [scope, setScope] = useState<"main" | "sub">("main");
  const [selectedSub, setSelectedSub] = useState<string | null>(null);

  // 拉取版本列表（仅首次 / 手动重试）。
  // refresh 不依赖 selectedVersion：用函数式 setSelectedVersion 读最新值，
  // 保持 refresh 引用稳定（空依赖），避免 effect 重跑导致重复请求。
  const refresh = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const snaps = await getSnapshots();
      setSnapshots(snaps);
      if (snaps.length > 0) {
        const prod = snaps.find((s) => s.status === "production") ?? snaps[0];
        // 函数式更新：仅在当前仍为 null 时设置默认选中，不覆盖用户已选
        setSelectedVersion((prev) => prev ?? prod.version);
      }
    } catch (err) {
      // DEC-005：报错态替代 toast + 空列表——旧账本数据比没数据危害大
      setLoadError(err instanceof Error ? err.message : "读取版本列表失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // 版本切换：并行拉主要素 + 记忆要素 + 升级 diff
  useEffect(() => {
    if (selectedVersion == null) return;
    setElements(null);
    setUpgradeDiff(null);
    setDiffFailed(false);

    Promise.all([
      getHarnessElements(selectedVersion).catch((err) => {
        toast.error(err instanceof Error ? err.message : "读取 Harness 要素失败");
        return null;
      }),
      getUpgradeDiff(selectedVersion).catch((err) => {
        // diff 失败不阻断要素展示（提示不阻断，REQ-20260923-145931 FR-003）；
        // 置失败态让总览条显示「变更计算失败」占位，而非停留假加载
        toast.error(err instanceof Error ? err.message : "读取升级 diff 失败");
        return { failed: true as const };
      }),
    ]).then(([els, diff]) => {
      setElements(els);
      if (diff && "failed" in diff) {
        setDiffFailed(true);
      } else {
        setUpgradeDiff(diff as UpgradeDiffView | null);
      }
    });
  }, [selectedVersion]);

  // 构建 diffs 查找表：agent 名 → AgentDiff
  const diffs = (() => {
    if (!upgradeDiff?.changes?.agents) return null;
    const map = new Map<string, AgentDiff>();
    for (const { agent, diff } of upgradeDiff.changes.agents) {
      map.set(agent, diff);
    }
    return map;
  })();

  // 主/Sub 分组（role 由后端清单/回退路径统一提供，缺失时兜底单主架构）
  const mainAgents = elements?.agents.filter((a) => (a.role ?? "main") === "main") ?? [];
  const subAgents = elements?.agents.filter((a) => a.role === "sub") ?? [];
  const activeSub =
    subAgents.find((a) => a.name === selectedSub) ?? subAgents[0] ?? null;
  // 当前视角喂给要素 Tab 的 agent 集合：主视角 = 全部主 agent；
  // Sub 视角 = 当前选中的单个子代理（五要素作用于选中者，DEC-004）
  const scopedAgents = scope === "main" ? mainAgents : activeSub ? [activeSub] : [];

  // 切版本时重置视角（新版本可能没有子代理，防止停留在空 Sub 视图）
  useEffect(() => {
    setScope("main");
    setSelectedSub(null);
  }, [selectedVersion]);

  if (loading) return <div className="page-loading">加载版本列表…</div>;

  // DEC-005：账本不可达报错态（含重试），不渲染任何版本数据
  if (loadError) {
    return (
      <div className="harness-page">
        <header className="page-header">
          <h1>Harness 要素</h1>
          <p className="page-desc">
            按版本查看本套的提示词、技能、工具、中间件和助手。
          </p>
        </header>
        <div className="evo-state-error">
          {loadError || "Platform 账本不可达"}
          <br />
          <button className="evo-retry-button" onClick={refresh}>重试</button>
        </div>
      </div>
    );
  }

  return (
    <div className="harness-page">
      <header className="page-header">
        <h1>Harness 要素</h1>
        <p className="page-desc">
          按版本查看本套的提示词、技能、工具、中间件和助手。
        </p>
      </header>

      {/* 版本选择 */}
      <div className="harness-version-bar">
        <label>选择版本：</label>
        <select
          className="evolve-select"
          value={selectedVersion ?? ""}
          onChange={(e) => setSelectedVersion(Number(e.target.value))}
        >
          {snapshots.map((s) => (
            <option key={s.version} value={s.version}>
              v{s.version} {s.status === "production" ? "（生产）" : ""}
              {s.same_code_as != null ? `（代码同 v${s.same_code_as}）` : ""} —{" "}
              {s.change_summary?.slice(0, 40) || "无说明"}
            </option>
          ))}
        </select>
      </div>

      {snapshots.find((s) => s.version === selectedVersion)?.status === "production" && <CandidateEditor />}

      {/* 升级总览条（diff 拉取失败显示占位，FR-003 提示不阻断） */}
      <UpgradeOverview diff={upgradeDiff} failed={diffFailed} />

      {elements ? (
        <>
          {/* 主/SubAgent 高层切换（DEC-003：Subagent 是一等视角，不是第六要素） */}
          <div className="harness-scope-bar" role="tablist" aria-label="Agent 视角切换">
            <button
              className={`harness-scope-toggle ${scope === "main" ? "active" : ""}`}
              onClick={() => setScope("main")}
            >
              主Agent（{mainAgents.map((a) => a.display_name ?? a.name).join("、") || "—"}）
            </button>
            <button
              className={`harness-scope-toggle ${scope === "sub" ? "active" : ""}`}
              onClick={() => setScope("sub")}
            >
              SubAgent（{subAgents.length}）
            </button>
          </div>

          {/* SubAgent 视图：子代理选择器（DEC-004）+ 未挂载孤儿（DEC-005 诚实呈现） */}
          {scope === "sub" && (
            <div className="harness-sub-selector">
              {subAgents.map((a) => (
                <button
                  key={a.name}
                  className={`harness-sub-chip ${activeSub?.name === a.name ? "active" : ""}`}
                  onClick={() => setSelectedSub(a.name)}
                  title={a.description}
                >
                  {a.display_name ?? a.name}
                  {a.runtime_name && a.runtime_name !== a.name ? `（${a.runtime_name}）` : ""}
                </button>
              ))}
              {(elements.unmounted?.subagents ?? []).map((path) => (
                <span key={path} className="harness-sub-chip unmounted" title="存在于包内但架构清单未挂载">
                  {path.replace(/^subagents\//, "").replace(/\.py$/, "")} · 未挂载
                </span>
              ))}
              {subAgents.length === 0 && (elements.unmounted?.subagents ?? []).length === 0 && (
                <span className="harness-sub-empty">此版本无子代理</span>
              )}
            </div>
          )}

          <Tabs
            key={`${scope}-${activeSub?.name ?? "main"}`}
            defaultValue="prompt"
            className="harness-tabs"
          >
            <TabsList>
              <TabsTrigger value="prompt">Prompt</TabsTrigger>
              <TabsTrigger value="skills">Skills</TabsTrigger>
              <TabsTrigger value="tools">Tools</TabsTrigger>
              <TabsTrigger value="middleware">Middleware</TabsTrigger>
            </TabsList>
            <TabsContent value="prompt">
              <PromptTab
                agents={scopedAgents}
                diffs={diffs}
                /* 草稿发版成功：切到新版本（触发要素重拉）+ 刷新版本下拉 */
                onReleased={(version) => {
                  setSelectedVersion(version);
                  refresh();
                }}
              />
            </TabsContent>
            <TabsContent value="skills">
              <SkillsTab agents={scopedAgents} diffs={diffs} />
            </TabsContent>
            <TabsContent value="tools">
              <ToolsTab
                tools={elements.tools}
                mountedTools={scope === "sub" ? activeSub?.tools ?? [] : undefined}
              />
            </TabsContent>
            <TabsContent value="middleware">
              <MiddlewareTab
                agents={scopedAgents}
                diffs={diffs}
                hasSource={elements.has_source}
              />
            </TabsContent>
          </Tabs>
        </>
      ) : (
        <div className="page-loading">加载 Harness 要素…</div>
      )}
    </div>
  );
}
