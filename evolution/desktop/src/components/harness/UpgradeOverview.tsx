import type { UpgradeDiffView, AgentDiff } from "@/lib/api";
import { AgentBadge } from "./AgentBadge";

/**
 * 升级总览条（页面门面，实时 git diff，REQ-20260923-145931 FR-003）。
 *
 * 选一个版本后，第一眼看到"这个版本相对代码基线改了什么"：
 * - base_kind=ancestor：遍历 changes.agents，每 agent 一行
 *   （prompt ±N 行 / skills ±N / middleware 增删改），基线版本号随标题展示
 * - base_kind=same_code：同代码重复发版（如回滚演练），显示「代码同 vX，无差异」
 * - base_kind=root：初始版本占位
 * - base_kind=error 或 failed（diff 拉取失败）：变更计算失败占位（不阻断五要素展示）
 */
export function UpgradeOverview({
  diff,
  failed = false,
}: {
  diff: UpgradeDiffView | null;
  failed?: boolean;
}) {
  if (failed) {
    return (
      <div className="upgrade-overview">
        <h3 className="upgrade-overview-title">📌 本版本升级</h3>
        <p className="upgrade-bootstrap">变更计算失败（diff 拉取异常），要素展示不受影响。</p>
      </div>
    );
  }

  if (!diff) {
    return (
      <div className="upgrade-overview">
        <h3 className="upgrade-overview-title">📌 本版本升级</h3>
        <p className="upgrade-bootstrap">计算升级差异…</p>
      </div>
    );
  }

  if (diff.base_kind === "root") {
    return (
      <div className="upgrade-overview">
        <h3 className="upgrade-overview-title">📌 本版本升级</h3>
        <p className="upgrade-bootstrap">这是初始版本，无升级对比。</p>
      </div>
    );
  }

  if (diff.base_kind === "same_code" && diff.same_code_as != null) {
    return (
      <div className="upgrade-overview">
        <h3 className="upgrade-overview-title">📌 本版本升级</h3>
        <p className="upgrade-bootstrap">
          代码同 v{diff.same_code_as}，与基线无差异（重复晋升 / 回滚类发版）。
        </p>
      </div>
    );
  }

  if (diff.base_kind === "error") {
    return (
      <div className="upgrade-overview">
        <h3 className="upgrade-overview-title">📌 本版本升级</h3>
        <p className="upgrade-bootstrap">变更计算失败（基线版本解析异常），要素展示不受影响。</p>
      </div>
    );
  }

  const agents = diff.changes.agents;
  if (agents.length === 0) {
    return (
      <div className="upgrade-overview">
        <h3 className="upgrade-overview-title">
          📌 本版本升级{diff.base_version != null && <span className="upgrade-base-tag">相对 v{diff.base_version}</span>}
        </h3>
        <p className="upgrade-bootstrap">本版本相对基线无要素变更。</p>
      </div>
    );
  }

  return (
    <div className="upgrade-overview">
      <h3 className="upgrade-overview-title">
        📌 本版本升级{diff.base_version != null && <span className="upgrade-base-tag">相对 v{diff.base_version}</span>}
      </h3>

      {/* 客观 diff 摘要 */}
      <div className="upgrade-summary-list">
        {agents.map(({ agent, diff: agentDiff }) => (
          <div key={agent} className="upgrade-summary-item">
            <AgentBadge name={agent} />
            <span className="upgrade-change-desc">{summarizeAgentDiff(agentDiff)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * 把单个 agent 的三要素 diff 压成一句人话摘要。
 * 否则分别说 prompt/skills/middleware。
 */
function summarizeAgentDiff(diff: AgentDiff): string {
  if (diff.whole_agent === "added") return "新增 Agent";
  if (diff.whole_agent === "removed") return "删除 Agent";

  const parts: string[] = [];

  if (diff.prompt) {
    const { added, removed } = diff.prompt.summary;
    parts.push(`prompt +${added}/-${removed} 行`);
  }

  if (diff.skills) {
    const a = diff.skills.added.length;
    const r = diff.skills.removed.length;
    if (a || r) parts.push(`skills +${a}/-${r}`);
  }

  if (diff.processors && diff.processors.length > 0) {
    const added = diff.processors.filter((p) => p.change_type === "added").length;
    const removed = diff.processors.filter((p) => p.change_type === "removed").length;
    const modified = diff.processors.filter((p) => p.change_type === "modified").length;
    const segs: string[] = [];
    if (added) segs.push(`${added} 新增`);
    if (removed) segs.push(`${removed} 删除`);
    if (modified) segs.push(`${modified} 修改`);
    if (segs.length) parts.push(`middleware ${segs.join(" / ")}`);
  }

  return parts.length ? parts.join("，") : "无变化";
}
