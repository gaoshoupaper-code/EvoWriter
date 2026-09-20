"""评测弱点报告（REQ-20260919-172934 / FR-005，DEC-004/009/010）。

batch 聚合视图（查询时生成，不设独立报告制品存储；进化接口以 batch_id 为引用单位）：
  - 维度层：5 主观维均分排名 + 缺陷标签命中统计（哪个标签被挂最多）
  - 个例层：总分最低的 case 清单（含失败条目）
  - 校准状态如实标注（uncalibrated——本次无校准工作流，需求风险处置约束）
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

    # 维度层：均分 + 标签命中
    dim_sums: dict[str, tuple[float, int]] = {}
    tag_hits: dict[str, int] = {}
    for item in parsed:
        data = item["data"]
        if not data:
            continue
        for key, value in (data.get("scores") or {}).items():
            if key in rubric_v3.DIMENSION_KEYS and isinstance(value, (int, float)) and value > 0:
                s, c = dim_sums.get(key, (0.0, 0))
                dim_sums[key] = (s + value, c + 1)
        for dim_tags in (data.get("tags") or {}).values():
            if isinstance(dim_tags, list):
                for tag in dim_tags:
                    if tag in rubric_v3.ALL_DEFECT_TAGS:
                        tag_hits[tag] = tag_hits.get(tag, 0) + 1

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
    tags = sorted(
        ({"tag": tag, "hits": hits} for tag, hits in tag_hits.items()),
        key=lambda t: -t["hits"],
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
                "tags": i["data"].get("tags", {}),
                "rule_delivery_passed": (i["data"].get("rule_delivery") or {}).get("passed"),
            }
            for i in scored
        ),
        key=lambda c: c["overall"] if isinstance(c["overall"], (int, float)) else 99,
    )[:LOW_CASE_LIMIT]

    rule_fail_count = sum(
        1 for i in scored if not (i["data"].get("rule_delivery") or {}).get("passed", True)
    )

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
        "tag_hits": tags,
        "rule_delivery_failed": rule_fail_count,
        "low_cases": low_cases,
        "failed_rows": [
            {"case_id": r["case_id"], "seed": r.get("seed"), "error": r["error"]}
            for r in failed_rows
        ],
    }


def build_summary_for_evolve(batch_id: str, limit: int = 5) -> dict[str, Any] | None:
    """给进化会话注入用的精简摘要（FR-006）：维度弱点排序 + 高频标签。

    返回 None 表示该 batch 无可聚合数据（调用方降级不阻断）。
    """
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
        "top_tags": report["tag_hits"][:limit],
        "rule_delivery_failed": report["rule_delivery_failed"],
    }


__all__ = ["build_report", "build_summary_for_evolve"]
