"""评测评分引擎（REQ-20260919-172934 / FR-002/003，TD-002；v4 重构 REQ-20260921-210038）。

评测评分链路（benchmark 唯一主链路）：
  1. load_outline_deliveries：从 ArtifactRevision 事件直读大纲三件套
     （不走卷宗编译、不走 eval_agent 旧直评路径——DEC-006 休眠链路零依赖）
  2. score_case：rubric v4 按维独立 judge 调用（五维并发，DEC-010）→
     两段理由校验（DEC-002/006）→ 规则项判定
  3. 单维失败仅重试该维 1 次，仍败上抛 DimensionScoreError（DEC-013），
     调用方按行级 failed 处理

轻量可信读取（TD-002）：只做 content_hash 自校验，不做卷宗级 event 全链校验——
评测消费的是冻结产物内容，可信链校验是卷宗（已休眠）的职责。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import app.core.db as db
from app.core import llm
from app.core.models import TraceLogEvent
from app.benchmark import rubric_v3
from app.trace_payloads import hydrate_event

logger = logging.getLogger("evolution.benchmark.scorer")

# 三件套分组规则（与 eval_extractor 的 storybuilding 分类同口径）
_GROUP_STORYLINE = ("主线 storyline", "storyline")
_GROUP_CHARACTER = ("人物 character", "character")
_GROUP_WORLDVIEW = ("世界观 worldview", "worldview")

# 规则项「交付完整」的判定参数
_MIN_GROUP_CHARS = 200          # 每组正文最小长度
_PLACEHOLDER_PAT = re.compile(r"TODO|待补充|待填|占位|\[placeholder\]", re.IGNORECASE)

# 单维 judge 调用尝试数（DEC-013：1 次 + 重试 1 次）
_DIM_ATTEMPTS = 2


# ── 三件套直读（ArtifactRevision 事件）──────────────────────


def _classify_logical_key(key: str) -> tuple[str, str] | None:
    """logical_key → (展示名, 组名)；非三件套文件返回 None。"""
    k = key.strip().lstrip("/").lower()
    if k == "storyline.md" or k.startswith("storyline/"):
        return _GROUP_STORYLINE
    if k.startswith("character/"):
        return _GROUP_CHARACTER
    if k == "worldview.md":
        return _GROUP_WORLDVIEW
    return None


def _select_latest_outline_revisions(trace_id: str) -> dict[str, dict[str, Any]]:
    """事件流直读：三件套 logical_key → 最新修订信息（评分与展示共用的选择逻辑）。

    事件按 sequence 后写覆盖（同 key 取最新）；content_hash 校验失败的修订
    记告警并跳过。返回 {logical_key: {display, content, content_hash,
    artifact_revision_id}}——load_outline_deliveries（评分输入）与
    load_outline_delivery_index（报告展示）经同一函数保证口径一致（DEC-002）。
    """
    rows = db.query_all(
        "SELECT payload_json FROM event_payloads "
        "WHERE trace_id=? AND type='artifact_revision' ORDER BY sequence",
        (trace_id,),
    )
    files: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            event = hydrate_event(TraceLogEvent.model_validate(json.loads(row["payload_json"])))
        except Exception:
            logger.warning("artifact_revision 事件解析失败 trace=%s", trace_id, exc_info=True)
            continue
        artifact = event.artifact or {}
        key = artifact.get("logical_key") or ""
        group = _classify_logical_key(key)
        if group is None:
            continue
        content = event.output.get("content") if isinstance(event.output, dict) else None
        if not isinstance(content, str) or not content.strip():
            continue
        expected_hash = artifact.get("content_hash")
        actual_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if isinstance(expected_hash, str) and actual_hash != expected_hash:
            logger.warning("artifact hash 不一致，跳过 trace=%s key=%s", trace_id, key)
            continue
        revision_id = getattr(event, "artifact_revision_id", None)
        files[key] = {
            "display": group[0],
            "content": content,
            "content_hash": expected_hash if isinstance(expected_hash, str) else None,
            "artifact_revision_id": revision_id,
        }
    return files


def load_outline_deliveries(trace_id: str) -> dict[str, str]:
    """从 trace 的 artifact_revision 事件读取大纲三件套（{展示名: 正文}）。

    同 logical_key 取最新 revision（事件按 sequence，后写覆盖）；
    正文经 hydrate 回填 + content_hash 校验，校验失败的 revision 记告警并跳过。
    """
    files = _select_latest_outline_revisions(trace_id)

    # 组内多文件按路径排序拼接
    grouped: dict[str, list[str]] = {}
    for key in sorted(files):
        info = files[key]
        grouped.setdefault(info["display"], []).append(f"### {key}\n\n{info['content']}")
    return {display: "\n\n".join(parts) for display, parts in grouped.items()}


def load_outline_delivery_index(trace_id: str) -> list[dict[str, Any]]:
    """三件套交付索引（REQ-20260921-114943 FR-002）：展示名分组 + 每文件最新修订元数据。

    修订选择与评分输入完全同源（_select_latest_outline_revisions，DEC-002）；
    可用性连接 artifact_revisions/payload_objects——正文已删除或过期（90 天，
    DEC-009）即 available=False，前端按「正文已过期」降级展示。
    三组固定顺序返回（主线/人物/世界观），缺失组 files=[] 供前端标注「缺失」。
    """
    from datetime import UTC, datetime

    files = _select_latest_outline_revisions(trace_id)

    rev_ids = [v["artifact_revision_id"] for v in files.values() if v["artifact_revision_id"]]
    meta: dict[str, dict[str, Any]] = {}
    if rev_ids:
        placeholders = ",".join("?" * len(rev_ids))
        for row in db.query_all(
            f"""SELECT r.artifact_revision_id, p.expires_at, p.deleted_at, p.size_bytes
                FROM artifact_revisions r
                LEFT JOIN payload_objects p ON p.payload_id = r.payload_id
                WHERE r.artifact_revision_id IN ({placeholders})""",
            tuple(rev_ids),
        ):
            meta[row["artifact_revision_id"]] = dict(row)

    now = datetime.now(UTC)

    def _available(m: dict[str, Any] | None) -> bool:
        if m is None:
            return False
        if m.get("deleted_at"):
            return False
        expires_at = m.get("expires_at")
        if not expires_at:
            return True
        try:
            return datetime.fromisoformat(expires_at) > now
        except ValueError:
            return False

    groups: dict[str, list[dict[str, Any]]] = {}
    for key in sorted(files):
        info = files[key]
        revision_id = info["artifact_revision_id"]
        m = meta.get(revision_id) if revision_id else None
        groups.setdefault(info["display"], []).append({
            "logical_key": key,
            "content_hash": info["content_hash"],
            "artifact_revision_id": revision_id,
            "size_bytes": m.get("size_bytes") if m else None,
            "expires_at": m.get("expires_at") if m else None,
            "available": _available(m),
        })

    return [
        {"display": display, "files": groups.get(display, [])}
        for display in (_GROUP_STORYLINE[0], _GROUP_CHARACTER[0], _GROUP_WORLDVIEW[0])
    ]


def check_delivery_complete(deliveries: dict[str, str]) -> dict[str, Any]:
    """规则项「交付完整」：三件套齐全、内容非空非占位（FR-002 ③，代码判）。"""
    problems: list[str] = []
    required = [_GROUP_STORYLINE[0], _GROUP_CHARACTER[0], _GROUP_WORLDVIEW[0]]
    for name in required:
        content = deliveries.get(name)
        if not content or not content.strip():
            problems.append(f"缺少 {name}")
            continue
        if len(content.strip()) < _MIN_GROUP_CHARS:
            problems.append(f"{name} 内容过短（< {_MIN_GROUP_CHARS} 字符）")
        if _PLACEHOLDER_PAT.search(content):
            problems.append(f"{name} 含占位符")
    return {
        "key": rubric_v3.RULE_DELIVERY_COMPLETE["key"],
        "passed": not problems,
        "problems": problems,
    }


# ── judge 评分（按维独立调用，DEC-010）─────────────────────


class DimensionScoreError(RuntimeError):
    """单维评分重试用尽（DEC-013）；message 含失败维度名，行级 error 由此携带。"""


def _parse_response(raw: str) -> dict[str, Any]:
    """解析 judge 返回 JSON（容错：剥离 markdown 代码块、提取首个 JSON 对象）。"""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError(f"无法解析 judge 返回为 JSON: {raw[:200]}")


def _validate_dim_judgement(judgement: dict[str, Any], dim_key: str) -> None:
    """校验单维 judge 输出契约（FR-004；失败 = 该维评分失败可重试）。

    - score 为 0-5 整数
    - 达标/不足均为非空字符串数组（5 分不足段允许且仅允许「未发现不足」占位）
    - score<5 时不足段不得是「未发现不足」占位（有差距才低于 5 分）
    """
    score = judgement.get("score")
    if not isinstance(score, int) or isinstance(score, bool) or not (0 <= score <= 5):
        raise ValueError(f"{dim_key} 分数非法: {score!r}")
    for field in ("达标", "不足"):
        items = judgement.get(field)
        if not isinstance(items, list) or not items:
            raise ValueError(f"{dim_key} 「{field}」缺失或为空数组")
        if not all(isinstance(i, str) and i.strip() for i in items):
            raise ValueError(f"{dim_key} 「{field}」存在空条目")
    flaws = judgement["不足"]
    if (
        score < 5
        and len(flaws) == 1
        and flaws[0].strip() == rubric_v3.NO_FLAW_PLACEHOLDER
    ):
        raise ValueError(f"{dim_key} 分数 {score} <5 但不足段为「{rubric_v3.NO_FLAW_PLACEHOLDER}」")


def _score_dimension_once(
    dim: dict[str, Any], demand_md: str, deliveries: dict[str, str],
    judge_config_id: int | None,
) -> dict[str, Any]:
    """单维 judge 调用 + 解析 + 校验（一次尝试）。"""
    messages = [
        {"role": "system", "content": rubric_v3.build_judge_dim_system_prompt(dim)},
        {"role": "user", "content": rubric_v3.build_judge_user_prompt(demand_md, deliveries)},
    ]
    # 300s：judge 非流式输出单维 JSON；输入含大纲三件套全文，与 executor/
    # evolution 侧 300s 先例对齐（deepseek 兼容端点 120s 常态撞线的既有教训）。
    raw = llm.chat(
        messages,
        temperature=0.0,
        timeout=300.0,
        phase=f"benchmark_score:{dim['key']}",
        scope="eval",
        config_id=judge_config_id,
    )
    judgement = _parse_response(raw)
    _validate_dim_judgement(judgement, dim["key"])
    return judgement


def _score_dimension(
    dim: dict[str, Any], demand_md: str, deliveries: dict[str, str],
    judge_config_id: int | None,
) -> dict[str, Any]:
    """单维评分：失败仅重试该维 1 次（DEC-013），仍败上抛 DimensionScoreError。"""
    last_error: Exception | None = None
    for attempt in range(1, _DIM_ATTEMPTS + 1):
        try:
            return _score_dimension_once(dim, demand_md, deliveries, judge_config_id)
        except Exception as exc:
            last_error = exc
            logger.warning(
                "评分维度「%s」第 %d 次尝试失败: %s", dim["key"], attempt, exc,
            )
    raise DimensionScoreError(
        f"维度「{dim['key']}」评分重试用尽（{_DIM_ATTEMPTS} 次）: {last_error}"
    ) from last_error


def score_case(
    demand_md: str, deliveries: dict[str, str], judge_config_id: int | None = None,
) -> dict[str, Any]:
    """对一个 case 的一次生成产物评分（5 次按维 judge 调用并发 + 规则项判定）。

    judge_config_id（FR-003）：指定 judge 配置（触发时下拉选择的）；
    None=默认解析（llm.chat scope=eval，未配置降级 evolution）。

    Returns: {
      rubric_version, calibration, anchor_status,
      scores: {维度: 分}, reasons: {维度: {达标: [...], 不足: [...]}}（DEC-006）,
      overall: 有效维度均分（score>0 参与）,
      rule_delivery: {passed, problems},
    }
    Raises: DimensionScoreError（单维重试用尽，含维度名）；调用方按行级 failed 处理。
    """
    with ThreadPoolExecutor(max_workers=len(rubric_v3.DIMENSIONS)) as pool:
        futures = {
            dim["key"]: pool.submit(
                _score_dimension, dim, demand_md, deliveries, judge_config_id,
            )
            for dim in rubric_v3.DIMENSIONS
        }
        judgements = {key: fut.result() for key, fut in futures.items()}

    scores = {key: judgements[key]["score"] for key in rubric_v3.DIMENSION_KEYS}
    reasons = {
        key: {"达标": judgements[key]["达标"], "不足": judgements[key]["不足"]}
        for key in rubric_v3.DIMENSION_KEYS
    }
    valid = [v for v in scores.values() if v > 0]
    overall = round(sum(valid) / len(valid), 2) if valid else 0.0
    return {
        "rubric_version": rubric_v3.RUBRIC_VERSION,
        "calibration": rubric_v3.CALIBRATION_STATUS,
        "anchor_status": rubric_v3.ANCHOR_DRAFT_STATUS,
        "scores": scores,
        "reasons": reasons,
        "overall": overall,
        "rule_delivery": check_delivery_complete(deliveries),
    }


__all__ = [
    "DimensionScoreError",
    "load_outline_deliveries",
    "load_outline_delivery_index",
    "check_delivery_complete",
    "score_case",
]
