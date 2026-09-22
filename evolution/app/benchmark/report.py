"""评测弱点报告（REQ-20260919-172934 / FR-005，DEC-004/009/010；v4 适配 REQ-20260921-210038）。

batch 聚合视图（查询时生成，不设独立报告制品存储；进化接口以 batch_id 为引用单位）：
  - 维度层：5 主观维均分排名（缺陷标签命中统计随词表下线，弱点信号在
    各行 reasons 的「不足」段，DEC-009）
  - 个例层：总分最低的 case 清单（含失败条目）
  - 校准状态如实标注（uncalibrated——实测达标前不翻转，DEC-007）
"""
from __future__ import annotations

import json
from typing import Any

import app.core.db as db
from app.benchmark import rubric_v3

# 个例层展示的最低分条数
LOW_CASE_LIMIT = 5


def build_report(batch_id: str) -> dict[str, Any]:
    """编译一个评测批次的弱点报告。"""
    rows = db.query_all(
        "SELECT * FROM benchmark_runs WHERE batch_id=? ORDER BY case_id, seed",
        (batch_id,),
    )
    if not rows:
        return {"batch_id": batch_id, "status": "not_found"}

    done_rows = [r for r in rows if r["status"] == "done"]
    failed_rows = [r for r in rows if r["status"] == "failed"]

    if not done_rows:
        return {
            "batch_id": batch_id,
            "status": "no_scored_data",
            "message": "无可聚合数据（批次无成功评分行）",
            "progress": {
                "total": len(rows), "done": 0, "failed": len(failed_rows),
                "active": len(rows) - len(failed_rows),
            },
        }

    # 解析全部评分行
    parsed: list[dict[str, Any]] = []
    for row in done_rows:
        try:
            data = json.loads(row["scores_json"]) if row.get("scores_json") else None
        except (json.JSONDecodeError, TypeError):
            data = None
        parsed.append({"row": row, "data": data})

    # 维度层：均分（标签命中统计已随词表下线，DEC-009 of REQ-20260921-210038）
    dim_sums: dict[str, tuple[float, int]] = {}
    for item in parsed:
        data = item["data"]
        if not data:
            continue
        for key, value in (data.get("scores") or {}).items():
            if key in rubric_v3.DIMENSION_KEYS and isinstance(value, (int, float)) and value > 0:
                s, c = dim_sums.get(key, (0.0, 0))
                dim_sums[key] = (s + value, c + 1)

    dimensions = sorted(
        (
            {
                "dimension": key,
                "mean": round(s / c, 4) if c else None,
                "n": c,
            }
            for key, (s, c) in dim_sums.items()
        ),
        key=lambda d: d["mean"] if d["mean"] is not None else 99,
    )

    # 个例层：总分最低的条目 + 失败条目
    scored = [i for i in parsed if i["data"]]
    low_cases = sorted(
        (
            {
                "case_id": i["row"]["case_id"],
                "seed": i["row"].get("seed"),
                "overall": i["data"].get("overall"),
                "scores": i["data"].get("scores", {}),
                "rule_delivery_passed": (i["data"].get("rule_delivery") or {}).get("passed"),
            }
            for i in scored
        ),
        key=lambda c: c["overall"] if isinstance(c["overall"], (int, float)) else 99,
    )[:LOW_CASE_LIMIT]

    rule_fail_count = sum(
        1 for i in scored if not (i["data"].get("rule_delivery") or {}).get("passed", True)
    )

    # 开销统计聚合（REQ-20260922-162823 FR-006 ③：报告并排呈现，不进胜负）
    cost = _aggregate_cost(done_rows)

    # 配比达成聚合（FR-005 ③）：full/semi 档行给达成率，minimal 档跳过
    quota = _aggregate_quota(scored)

    # 留白档分层（DEC-007 辅判读：blank_level 三档方向性观察，不判显著性）
    blank_layers = _aggregate_blank_layers(scored)

    # 指纹与校准标注（FR-005 ③：报告标注 rubric 版本与校准状态）
    return {
        "batch_id": batch_id,
        "status": "ok",
        "progress": {
            "total": len(rows), "done": len(done_rows),
            "failed": len(failed_rows), "active": len(rows) - len(done_rows) - len(failed_rows),
        },
        "fingerprints": {
            "golden_revision": rows[0]["golden_revision"],
            "rubric_version": rows[0].get("rubric_version"),
            "judge_fp": rows[0].get("judge_fp"),
            "model_fp": rows[0].get("model_fp"),
            "manifest_fp": rows[0].get("manifest_fp"),
            "platform_manifest_id": rows[0].get("platform_manifest_id"),
            "harness_version": rows[0]["harness_version"],
        },
        "calibration": rubric_v3.CALIBRATION_STATUS,
        "anchor_status": rubric_v3.ANCHOR_DRAFT_STATUS,
        "dimensions": dimensions,
        "rule_delivery_failed": rule_fail_count,
        "cost": cost,
        "quota": quota,
        "blank_layers": blank_layers,
        "low_cases": low_cases,
        "failed_rows": [
            {"case_id": r["case_id"], "seed": r.get("seed"), "error": r["error"]}
            for r in failed_rows
        ],
    }


def _aggregate_cost(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """批次开销聚合：合计与均摊（NULL 行不参与均值计数，如实保留 None）。"""
    def _nums(field: str) -> list[int]:
        return [r[field] for r in rows if isinstance(r.get(field), int)]

    fields = ("input_tokens", "output_tokens", "llm_calls", "wall_clock_ms")
    out: dict[str, Any] = {}
    for field in fields:
        values = _nums(field)
        out[field] = {
            "total": sum(values) if values else None,
            "mean": round(sum(values) / len(values), 1) if values else None,
            "n": len(values),
        }
    return out


def _aggregate_quota(scored: list[dict[str, Any]]) -> dict[str, Any]:
    """配比达成聚合：达标行数 / 已核对行数 + 逐行摘要（minimal 档跳过不计入）。"""
    checked: list[dict[str, Any]] = []
    skipped = 0
    passed = 0
    for item in scored:
        rule = item["data"].get("rule_quota") if item["data"] else None
        if not isinstance(rule, dict):
            continue
        if rule.get("status") == "skipped_minimal":
            skipped += 1
            continue
        if rule.get("passed"):
            passed += 1
        checked.append({
            "case_id": item["row"]["case_id"],
            "seed": item["row"].get("seed"),
            "passed": rule.get("passed"),
            "summary": rule.get("summary", ""),
        })
    return {
        "checked": len(checked),
        "passed": passed,
        "skipped_minimal": skipped,
        "rows": checked,
    }


def _aggregate_blank_layers(scored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """留白档分层均分（DEC-007：方向性辅判读，样本少不判显著性）。"""
    from app.common import evalset

    layers: dict[str, list[float]] = {}
    for item in scored:
        case_id = item["row"]["case_id"]
        overall = item["data"].get("overall") if item["data"] else None
        if not isinstance(overall, (int, float)):
            continue
        try:
            demand_md = evalset.load_case_demand(case_id, layer="golden")
        except Exception:
            continue
        level = evalset.parse_blank_level(demand_md)
        if level is None:
            continue
        layers.setdefault(level, []).append(float(overall))
    return [
        {
            "blank_level": level,
            "mean_overall": round(sum(layers[level]) / len(layers[level]), 4),
            "n": len(layers[level]),
        }
        for level in ("full", "semi", "minimal")
        if layers.get(level)
    ]


def build_summary_for_evolve(batch_id: str, limit: int = 5) -> dict[str, Any] | None:
    """给进化会话注入用的精简摘要（FR-006）：维度弱点排序（词表已下线，无标签信号）。"""
    report = build_report(batch_id)
    if report.get("status") != "ok":
        return None
    return {
        "batch_id": batch_id,
        "progress": report["progress"],
        "calibration": report["calibration"],
        "weakest_dimensions": [
            d for d in report["dimensions"][:limit]
        ],
        "rule_delivery_failed": report["rule_delivery_failed"],
    }


__all__ = ["build_report", "build_summary_for_evolve"]
