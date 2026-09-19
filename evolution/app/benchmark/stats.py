"""评测版本对比统计（REQ-20260919-172934 / FR-004，DEC-007/014）。

吸收原 A/B 双臂机制的统计框架：Welch t 检验（不等方差）+ 置信区间三态判定。
tie 是显式诚实态——「测不出差异」≠「没有差异」，不硬下结论。

无 scipy 依赖（TD-003）：Welch-Satterthwaite 自由度 + t 临界值表线性插值。
"""
from __future__ import annotations

import json
import math
from typing import Any

import app.core.db as db

# 有效评分低于此值时标注「统计力不足」（FR-004 失败语义）
MIN_SAMPLES_FOR_POWER = 10

# 单侧 95% t 临界值表（df → t），表外 df 线性插值，>120 按 ∞ 处理（TD-003）
_T_TABLE_95_ONE_SIDED: list[tuple[float, float]] = [
    (1.0, 6.314), (2.0, 2.920), (3.0, 2.353), (4.0, 2.132), (5.0, 2.015),
    (6.0, 1.943), (7.0, 1.895), (8.0, 1.860), (9.0, 1.833), (10.0, 1.812),
    (12.0, 1.782), (15.0, 1.753), (20.0, 1.725), (25.0, 1.708), (30.0, 1.697),
    (40.0, 1.684), (60.0, 1.671), (120.0, 1.658), (float("inf"), 1.645),
]


def _t_critical_95(df: float) -> float:
    """单侧 95% t 临界值（表插值）。"""
    if df <= 0:
        return _T_TABLE_95_ONE_SIDED[0][1]
    prev_df, prev_t = _T_TABLE_95_ONE_SIDED[0]
    for table_df, table_t in _T_TABLE_95_ONE_SIDED[1:]:
        if df <= table_df:
            frac = (df - prev_df) / (table_df - prev_df)
            return prev_t + frac * (table_t - prev_t)
        prev_df, prev_t = table_df, table_t
    return prev_t


def welch_compare(
    candidate: list[float], production: list[float],
) -> dict[str, Any]:
    """Welch t 检验：候选总分单侧 95% CI + 三态判定（DEC-014）。

    判定规则：候选 CI 下界 > 生产均值 = win；区间重叠 = tie；
    候选 CI 上界 < 生产均值 = lose。总均分定结论，维度不在此判。
    """
    n1, n2 = len(candidate), len(production)
    if n1 < 2 or n2 < 2:
        return {
            "verdict": "insufficient",
            "reason": f"样本不足（candidate={n1}, production={n2}，各需 ≥2）",
            "n_candidate": n1, "n_production": n2,
        }
    m1 = sum(candidate) / n1
    m2 = sum(production) / n2
    v1 = sum((x - m1) ** 2 for x in candidate) / (n1 - 1)
    v2 = sum((x - m2) ** 2 for x in production) / (n2 - 1)

    se_term = v1 / n1 + v2 / n2
    if se_term == 0:
        # 两组各自零方差（分数完全相同）：差异退化为均值比较
        ci_low = ci_high = m1
        df = float("inf")
    else:
        df = se_term ** 2 / (
            (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1)
        )
        t_crit = _t_critical_95(df)
        ci_low, ci_high = m1 - t_crit * math.sqrt(se_term), m1 + t_crit * math.sqrt(se_term)

    if ci_low > m2:
        verdict = "win"
    elif ci_high < m2:
        verdict = "lose"
    else:
        verdict = "tie"

    return {
        "verdict": verdict,
        "n_candidate": n1, "n_production": n2,
        "mean_candidate": round(m1, 4), "mean_production": round(m2, 4),
        "ci_95_low": round(ci_low, 4), "ci_95_high": round(ci_high, 4),
        "df": round(df, 2) if math.isfinite(df) else None,
        "delta_mean": round(m1 - m2, 4),
        "sufficient_power": n1 >= MIN_SAMPLES_FOR_POWER and n2 >= MIN_SAMPLES_FOR_POWER,
    }


# ── 指纹前置校验（FR-004 ①②）───────────────────────────────


_FINGERPRINT_FIELDS = ("golden_revision", "rubric_version", "judge_fp", "model_fp")


def _batch_done_rows(batch_id: str) -> list[dict[str, Any]]:
    return db.query_all(
        "SELECT * FROM benchmark_runs WHERE batch_id=? AND status='done'",
        (batch_id,),
    )


def _batch_field_values(rows: list[dict[str, Any]], field: str) -> list[Any]:
    return [row[field] for row in rows]


def check_comparable(rows_a: list[dict], rows_b: list[dict]) -> list[str]:
    """对比前置校验：四类指纹全同才可比（manifest 允许不同 = 比 harness 的场景）。

    model_fp 相同而 manifest_fp 不同 → 差异只能来自 harness commit，正是
    「同模型比 prompt 版本」的合法对比（FR-004 ②）。
    Returns: 不一致项描述列表；空列表 = 可比。
    """
    problems: list[str] = []
    for field in _FINGERPRINT_FIELDS:
        for label, rows in (("A", rows_a), ("B", rows_b)):
            values = set(_batch_field_values(rows, field))
            if len(values) > 1:
                problems.append(f"batch {label} 内 {field} 不一致（{sorted(values)}）——配置中途漂移")
        va, vb = set(_batch_field_values(rows_a, field)), set(_batch_field_values(rows_b, field))
        if va != vb:
            problems.append(f"两 batch {field} 不同：{sorted(va)} vs {sorted(vb)}")
    return problems


def compare_batches(batch_a: str, batch_b: str) -> dict[str, Any]:
    """对比两个评测批次：指纹校验 → Welch CI 三态 + 维度对照。

    batch_b 视为 production 基线，batch_a 视为候选（语义由调用方赋予）。
    """
    rows_a = _batch_done_rows(batch_a)
    rows_b = _batch_done_rows(batch_b)
    if not rows_a or not rows_b:
        return {
            "comparable": False,
            "problems": [f"batch {'A' if not rows_a else 'B'} 无 done 行，无法对比"],
        }

    problems = check_comparable(rows_a, rows_b)
    if problems:
        return {"comparable": False, "problems": problems}

    def _overalls(rows: list[dict]) -> list[float]:
        values = []
        for row in rows:
            if not row.get("scores_json"):
                continue
            try:
                overall = json.loads(row["scores_json"]).get("overall")
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(overall, (int, float)):
                values.append(float(overall))
        return values

    cand_scores, prod_scores = _overalls(rows_a), _overalls(rows_b)
    stats = welch_compare(cand_scores, prod_scores)

    # 维度均分对照（只报告不判胜，DEC-014）
    from app.benchmark import rubric_v3
    dim_stats: dict[str, dict[str, Any]] = {}
    for rows, label in ((rows_a, "candidate"), (rows_b, "production")):
        sums: dict[str, tuple[float, int]] = {}
        for row in rows:
            if not row.get("scores_json"):
                continue
            try:
                scores = json.loads(row["scores_json"]).get("scores", {})
            except (json.JSONDecodeError, TypeError):
                continue
            for key in rubric_v3.DIMENSION_KEYS:
                v = scores.get(key)
                if isinstance(v, (int, float)) and v > 0:
                    s, c = sums.get(key, (0.0, 0))
                    sums[key] = (s + v, c + 1)
        for key, (s, c) in sums.items():
            dim_stats.setdefault(key, {})[label] = {
                "mean": round(s / c, 4) if c else None, "n": c,
            }

    return {
        "comparable": True,
        "batch_a": batch_a, "batch_b": batch_b,
        "manifest_fp_a": rows_a[0].get("manifest_fp"),
        "manifest_fp_b": rows_b[0].get("manifest_fp"),
        "total": stats,
        "dimensions": dim_stats,
    }


__all__ = ["welch_compare", "check_comparable", "compare_batches", "MIN_SAMPLES_FOR_POWER"]
