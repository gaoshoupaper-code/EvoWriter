import { Fragment, useEffect, useState } from "react";
import { toast } from "sonner";
import { getBenchmarkRubric, type BenchmarkRubric } from "@/lib/api";

/**
 * 评测规则页（REQ-20260921-135543 FR-007，DEC-003/021；v4 适配 REQ-20260921-210038）。
 *
 * rubric 只读展示：头部版本/校准状态/只读说明 →
 * 评分纪律条款 → 五维卡片（判定问题/1-5 五档锚点）→ 交付完整规则项 → 评分制说明。
 * rubric 是代码常量单一事实源（改规则走代码发版，不做在线编辑）。
 */
const ANCHOR_LEVELS = ["5", "4", "3", "2", "1"] as const;

export default function BenchRules() {
  const [rubric, setRubric] = useState<BenchmarkRubric | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getBenchmarkRubric()
      .then((r) => setRubric(r))
      .catch((e: unknown) => {
        const msg = e instanceof Error ? e.message : String(e);
        setError(msg);
        toast.error(`读取评分标准失败：${msg}`);
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="bench-page bench-rules-page">
      <header className="page-header">
        <h1>评测规则</h1>
        <p className="page-desc">当前评分标准全文（只读 · 改动走代码发版）</p>
      </header>

      {loading ? (
        <div className="page-loading">加载评分标准…</div>
      ) : error ? (
        <div className="error-card">
          <span className="error-icon">⚠</span>
          <div className="error-body">
            <div className="error-title">评分标准加载失败</div>
            <div className="error-desc">{error}</div>
          </div>
        </div>
      ) : rubric ? (
        <>
          <div className="bench-rules-header">
            <span className="bench-rules-version mono">{rubric.rubric_version}</span>
            <span className={`bench-rules-badge ${rubric.calibration_status === "calibrated" ? "ok" : "warn"}`}>
              校准状态：{rubric.calibration_status}
            </span>
            <span className="bench-rules-badge warn">锚点：{rubric.anchor_status}</span>
            <span className="bench-rules-note">
              只读——评分标准是代码常量单一事实源，修改需改代码发版
            </span>
          </div>

          <div className="bench-rules-scoring bench-rules-discipline">
            <h4>评分纪律（judge 每次评分必须遵守）</h4>
            <ol>
              {rubric.discipline_rules.map((rule, i) => (
                <li key={i}>{rule}</li>
              ))}
            </ol>
          </div>

          <div className="bench-rules-grid">
            {rubric.dimensions.map((dim) => (
              <article key={dim.key} className="bench-rules-dim">
                <h4>{dim.key}</h4>
                <p className="bench-rules-question">{dim.question}</p>
                <dl className="bench-rules-anchors">
                  {ANCHOR_LEVELS.map((level) => (
                    <Fragment key={level}>
                      <dt>{level} 分</dt>
                      <dd>{dim.anchors[level]}</dd>
                    </Fragment>
                  ))}
                </dl>
              </article>
            ))}
          </div>

          <div className="bench-rules-rule">
            规则项「{rubric.rule_delivery.key}」（代码判定，不走 LLM）：
            {rubric.rule_delivery.description}。
          </div>

          <div className="bench-rules-scoring">
            <h4>评分制说明</h4>
            <ul>
              <li>五维各 1–5 整数分，每维独立调用 judge 评判；0 分 = 无法判断（不作质量结论）。</li>
              <li>总分 = 五维均值。</li>
              <li>每个维度不论几分都必有两段式理由：达标点（引原文佐证）+ 不足点；5 分不足段可为「未发现不足」。</li>
              <li>分数只用于同指纹前缀的相对对比（校准完成前绝对值不作质量结论）。</li>
            </ul>
          </div>
        </>
      ) : (
        <div className="monitor-empty">暂无评分标准数据</div>
      )}
    </div>
  );
}
