import { useEffect, useState, useCallback } from "react";
import { toast } from "sonner";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  getSnapshots,
  getHarnessElements,
  getMemoryElements,
  getUpgradeDiff,
  type Snapshot,
  type HarnessElementsView,
  type MemoryElementView,
  type UpgradeDiffView,
  type AgentDiff,
} from "@/lib/api";
import { UpgradeOverview } from "@/components/harness/UpgradeOverview";
import { MemorySubsystemCard } from "@/components/harness/MemorySubsystemCard";
import { PromptTab } from "@/components/harness/PromptTab";
import { SkillsTab } from "@/components/harness/SkillsTab";
import { ToolsTab } from "@/components/harness/ToolsTab";
import { MiddlewareTab } from "@/components/harness/MiddlewareTab";

/**
 * Harness 要素透视页（数据源：Platform 账本，REQ-20260923-145931）。
 *
 * 选一个版本快照 → 一眼看懂这个版本的 harness 怎么搭的：
 *   Prompt（说什么）/ Skills（会什么）/ Tools（用什么）/ Middleware（怎么装配）/ Memory（记忆怎么转）
 *
 * Tab 顺序按 Agent 构造的递进。记忆子系统保留 4 阶段流水线叙事，独立接口拉取。
 * 数据流：并行调 getHarnessElements（主要素）+ getMemoryElements（记忆要素）
 *       + getUpgradeDiff（升级总览实时 git diff，基线 = 代码基于的版本）。
 *
 * 账本不可达（DEC-005）：整页报错态 + 重试，不渲染版本下拉——
 * 宁缺勿错，不回退冻结 registry 旧数据。
 */
export default function HarnessPage() {
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [elements, setElements] = useState<HarnessElementsView | null>(null);
  const [memoryElements, setMemoryElements] = useState<MemoryElementView[] | null>(null);
  const [upgradeDiff, setUpgradeDiff] = useState<UpgradeDiffView | null>(null);
  const [diffFailed, setDiffFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

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
    setMemoryElements(null);
    setUpgradeDiff(null);
    setDiffFailed(false);

    Promise.all([
      getHarnessElements(selectedVersion).catch((err) => {
        toast.error(err instanceof Error ? err.message : "读取 Harness 要素失败");
        return null;
      }),
      getMemoryElements(selectedVersion).catch((err) => {
        // 记忆要素失败不阻断主要素展示（老版本无此接口/无 NWM），但提示用户
        toast.error(err instanceof Error ? err.message : "读取记忆要素失败");
        return null;
      }),
      getUpgradeDiff(selectedVersion).catch((err) => {
        // diff 失败不阻断要素展示（提示不阻断，REQ-20260923-145931 FR-003）；
        // 置失败态让总览条显示「变更计算失败」占位，而非停留假加载
        toast.error(err instanceof Error ? err.message : "读取升级 diff 失败");
        return { failed: true as const };
      }),
    ]).then(([els, memEls, diff]) => {
      setElements(els);
      setMemoryElements(memEls?.elements ?? []);
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

  if (loading) return <div className="page-loading">加载版本列表…</div>;

  // DEC-005：账本不可达报错态（含重试），不渲染任何版本数据
  if (loadError) {
    return (
      <div className="harness-page">
        <header className="page-header">
          <h1>Harness 要素</h1>
          <p className="page-desc">
            透视各版本 harness 的可进化要素，按 Prompt / Skills / Tools / Middleware / Memory 分层展示
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
          透视各版本 harness 的可进化要素，按 Prompt / Skills / Tools / Middleware / Memory 分层展示
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

      {/* 升级总览条（diff 拉取失败显示占位，FR-003 提示不阻断） */}
      <UpgradeOverview diff={upgradeDiff} failed={diffFailed} />

      {/* 五要素 Tab：Prompt → Skills → Tools → Middleware → Memory（构造递进顺序） */}
      {elements ? (
        <Tabs defaultValue="prompt" className="harness-tabs">
          <TabsList>
            <TabsTrigger value="prompt">Prompt</TabsTrigger>
            <TabsTrigger value="skills">Skills</TabsTrigger>
            <TabsTrigger value="tools">Tools</TabsTrigger>
            <TabsTrigger value="middleware">Middleware</TabsTrigger>
            <TabsTrigger value="memory">Memory</TabsTrigger>
          </TabsList>
          <TabsContent value="prompt">
            <PromptTab agents={elements.agents} diffs={diffs} />
          </TabsContent>
          <TabsContent value="skills">
            <SkillsTab agents={elements.agents} diffs={diffs} />
          </TabsContent>
          <TabsContent value="tools">
            <ToolsTab tools={elements.tools} />
          </TabsContent>
          <TabsContent value="middleware">
            <MiddlewareTab
              agents={elements.agents}
              diffs={diffs}
              hasSource={elements.has_source}
            />
          </TabsContent>
          <TabsContent value="memory">
            {/* memoryElements 未到位时显示加载态，到位后由组件内部处理空态/流水线 */}
            {/* version 用于卡片内"查看源码"懒加载 /snapshots/{v}/source */}
            {memoryElements && selectedVersion != null ? (
              <MemorySubsystemCard elements={memoryElements} version={selectedVersion} />
            ) : (
              <div className="page-loading">加载记忆要素…</div>
            )}
          </TabsContent>
        </Tabs>
      ) : (
        <div className="page-loading">加载 Harness 要素…</div>
      )}
    </div>
  );
}
