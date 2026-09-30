import { useMemo } from "react";

import { groupsForDimension } from "@/components/bench/DeliveriesView";
import type { BenchmarkRunRow, JudgeGroupDetail } from "@/lib/api";

/**
 * 行评分共享组件（REQ-20260923-131103 FR-004：产物页与批次详情页复用）。
 *
 * 从 CaseRunsPanel 抽出：五维概览条、维度卡片（两段理由/旧版兼容/交付跳转）、
 * seed 对比表、状态标签。渲染口径与批次详情页完全一致，无双标。
 *
 * 组评分（REQ-20260930-162207/FR-005）：judgeGroup 存在时维度卡改为
 * per-judge 理由分组（DEC-006 默认折叠）+ judge 极差 ≥2 高亮（DEC-007，
 * 与 seed 极差同口径）+ 缺席标注（DEC-003）；旧数据走原渲染路径。
 */

export const STATUS_LABEL: Record<string, string> = {
  done: "完成",
  failed: "失败",
  pending: "未跑",
  running: "运行中",
  evaluating: "评分中",
  cancelled: "已取消",
};

/** seed 间同维极差达到该值视为不稳定（1-5 分制下 2 分 = 跨档波动）。
 *  judge 间分歧高亮沿用同一阈值（DEC-007 of 162207）。 */
export const UNSTABLE_SPREAD = 2;

/** 分数 → 红/黄/绿色阶类名（≤2 红、≤3 黄、其余绿）。 */
export function scoreClass(value: number): string {
  if (value <= 2) return "bench-dim-low";
  if (value <= 3) return "bench-dim-mid";
  return "bench-dim-high";
}

/** 五维概览（DEC-009 of REQ-20260921-135543：条形，扫一眼）。 */
export function ScoreOverview({ scores }: { scores: Record<string, number> }) {
  return (
    <div className="bench-score-overview">
      {Object.entries(scores).map(([dim, v]) => (
        <div key={dim} className="bench-score-overview-row">
          <span className="bench-score-overview-dim">{dim}</span>
          <div className="bench-score-bar">
            <div className="bench-score-fill" style={{ width: `${(v / 5) * 100}%` }} />
          </div>
          <span className={`bench-score-overview-value ${scoreClass(v)}`}>
            {v.toFixed(1)}
          </span>
        </div>
      ))}
    </div>
  );
}

/** 单套两段理由块（组评分 per-judge 分组与旧版单套共用渲染）。 */
function ReasonLists({ reason }: { reason: { 达标: string[]; 不足: string[] } }) {
  return (
    <div className="bench-dim-reasons">
      <div className="bench-dim-reason-list bench-dim-reason-met">
        <span className="bench-dim-reason-label">✓ 达标</span>
        <ul>
          {reason.达标.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      </div>
      <div className="bench-dim-reason-list bench-dim-reason-flaw">
        <span className="bench-dim-reason-label">⚠ 不足</span>
        <ul>
          {reason.不足.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/**
 * 维度卡片：分值 + 理由。三条渲染路径（DEC-008 of 162207 按字段存在性分派）：
 * 1. judgeGroup 存在（组评分批次）：per-judge 分组折叠 + 分歧高亮 + 缺席标注
 * 2. reason 为对象（v4 单 judge 旧批次）：两段理由展开分色
 * 3. reason 为字符串/缺失（v3 及更早）：旧版理由原样展示
 */
export function DimensionCard({
  dim,
  value,
  reason,
  judgeGroup,
  onJump,
}: {
  dim: string;
  value: number;
  reason?: string | { 达标: string[]; 不足: string[] };
  /** 组评分明细（存在即组批次；旧批次不传走原路径）。 */
  judgeGroup?: JudgeGroupDetail | null;
  onJump: (hint: string | null) => void;
}) {
  const hint = groupsForDimension(dim);

  // judge 间分歧（DEC-007）：同维极差 ≥2 高亮；悬停显示各 judge 原始分
  const dimJudges = useMemo(
    () =>
      (judgeGroup?.members ?? []).map((m) => ({
        name: m.name,
        model: m.model,
        score: m.scores?.[dim],
        reason: m.reasons?.[dim] ?? null,
      })),
    [judgeGroup, dim],
  );
  const presentScores = dimJudges
    .map((d) => d.score)
    .filter((s): s is number => typeof s === "number");
  const spread =
    presentScores.length >= 2
      ? Math.max(...presentScores) - Math.min(...presentScores)
      : null;
  const judgeUnstable = spread != null && spread >= UNSTABLE_SPREAD;
  const hoverDetail =
    dimJudges.length > 0
      ? dimJudges
          .map((d) => `${d.name}: ${typeof d.score === "number" ? d.score : "缺席"}`)
          .join(" / ")
      : undefined;

  return (
    <div className="bench-dim-card">
      <div className="bench-dim-head">
        <span className="bench-dim-name">{dim}</span>
        <span
          className={`bench-dim-score ${scoreClass(value)} ${judgeUnstable ? "bench-seed-unstable" : ""}`}
          title={hoverDetail}
        >
          {value.toFixed(1)}/5
          {judgeUnstable && `（judge 极差 ${spread!.toFixed(0)}）`}
        </span>
      </div>

      {dimJudges.length > 0 ? (
        // 组评分：理由按 judge 分组、默认折叠（DEC-006）；缺席 judge 标注（DEC-003）
        <div className="bench-judge-blocks">
          {dimJudges.map((d, i) => (
            <details key={i} className="bench-judge-block">
              <summary>
                <span className="bench-judge-name">{d.name}</span>
                <span className="bench-judge-model">{d.model}</span>
                {typeof d.score === "number" ? (
                  <span className={`bench-judge-score ${scoreClass(d.score)}`}>{d.score}/5</span>
                ) : (
                  <span className="bench-judge-absent">缺席（评分失败，未计入均分）</span>
                )}
              </summary>
              {d.reason ? (
                <ReasonLists reason={d.reason} />
              ) : (
                <p className="bench-reason-absent">该维缺席，无理由。</p>
              )}
            </details>
          ))}
        </div>
      ) : reason == null ? (
        <p className="bench-reason-absent">旧版规则评分，未生成理由</p>
      ) : typeof reason === "string" ? (
        <div className="bench-dim-reasons">
          <p className="bench-dim-reason bench-dim-reason-legacy">
            <span className="bench-reason-legacy-tag">旧版理由</span>
            {reason}
          </p>
        </div>
      ) : (
        <ReasonLists reason={reason} />
      )}
      <button
        type="button"
        className="action-link"
        onClick={() => onJump(hint ? hint[0] : null)}
      >
        查看相关交付{hint ? `（${hint[0]}）` : "（全部三件套）"}
      </button>
    </div>
  );
}

/** seed 并排对比（DEC-010：行=维度，列=seed；同维极差 ≥2 高亮）。 */
export function SeedCompareTable({ rows }: { rows: BenchmarkRunRow[] }) {
  const dims = useMemo(() => {
    const seen: string[] = [];
    for (const r of rows) {
      for (const dim of Object.keys(r.scores?.scores ?? {})) {
        if (!seen.includes(dim)) seen.push(dim);
      }
    }
    return seen;
  }, [rows]);

  if (dims.length === 0) return null;

  return (
    <div className="bench-seed-compare">
      <h5>seed 对比（红格 = 同维极差 ≥{UNSTABLE_SPREAD}，输出不稳定信号）</h5>
      <table className="data-table">
        <thead>
          <tr>
            <th>维度</th>
            {rows.map((r) => (
              <th key={r.id}>
                #{r.seed ?? "-"}
                <span className="bench-seed-col-status"> {STATUS_LABEL[r.status] ?? r.status}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {dims.map((dim) => {
            const nums = rows
              .map((r) => r.scores?.scores?.[dim])
              .filter((v): v is number => typeof v === "number");
            const spread =
              nums.length >= 2 ? Math.max(...nums) - Math.min(...nums) : null;
            const unstable = spread != null && spread >= UNSTABLE_SPREAD;
            return (
              <tr key={dim}>
                <td>{dim}</td>
                {rows.map((r) => {
                  const v = r.scores?.scores?.[dim];
                  return (
                    <td
                      key={r.id}
                      className={unstable && typeof v === "number" ? "bench-seed-unstable" : ""}
                      title={unstable ? `极差 ${spread?.toFixed(1)} 分` : undefined}
                    >
                      {typeof v === "number" ? v.toFixed(1) : "—"}
                    </td>
                  );
                })}
              </tr>
            );
          })}
          <tr>
            <td>总分</td>
            {rows.map((r) => (
              <td key={r.id}>
                {r.scores?.overall != null ? r.scores.overall.toFixed(2) : "—"}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  );
}
