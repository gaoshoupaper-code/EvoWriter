"""评测评分引擎（REQ-20260919-172934 / FR-002/003，TD-002）。

评测评分链路（benchmark 唯一主链路）：
  1. load_outline_deliveries：从 ArtifactRevision 事件直读大纲三件套
     （不走卷宗编译、不走 eval_agent 旧直评路径——DEC-006 休眠链路零依赖）
  2. score_case：rubric v3 judge prompt → llm.chat(scope=eval) → 解析校验 → 规则项判定

轻量可信读取（TD-002）：只做 content_hash 自校验，不做卷宗级 event 全链校验——
评测消费的是冻结产物内容，可信链校验是卷宗（已休眠）的职责。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
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


def load_outline_deliveries(trace_id: str) -> dict[str, str]:
    """从 trace 的 artifact_revision 事件读取大纲三件套（{展示名: 正文}）。

    同 logical_key 取最新 revision（事件按 sequence，后写覆盖）；
    正文经 hydrate 回填 + content_hash 校验，校验失败的 revision 记告警并跳过。
    """
    rows = db.query_all(
        "SELECT payload_json FROM event_payloads "
        "WHERE trace_id=? AND type='artifact_revision' ORDER BY sequence",
        (trace_id,),
    )
    files: dict[str, tuple[str, str]] = {}  # logical_key → (展示名, 正文)
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
        files[key] = (group[0], content)

    # 组内多文件按路径排序拼接
    grouped: dict[str, list[str]] = {}
    for key in sorted(files):
        display, content = files[key]
        grouped.setdefault(display, []).append(f"### {key}\n\n{content}")
    return {display: "\n\n".join(parts) for display, parts in grouped.items()}


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


# ── judge 评分 ─────────────────────────────────────────────


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


def _validate_judgement(judgement: dict[str, Any]) -> None:
    """校验 judge 输出契约（FR-002 失败语义：校验不过 = 评分失败可重试）。

    - scores 五维齐全，值为 0-5 整数
    - 分数 ≤2 的维度必须有词表内标签 + 理由
    - 各维 tags 必须是该维词表子集（闭合校验，DEC-010）
    """
    scores = judgement.get("scores")
    if not isinstance(scores, dict):
        raise ValueError("judge 输出缺 scores 对象")
    tags = judgement.get("tags") or {}
    reasons = judgement.get("reasons") or {}
    if not isinstance(tags, dict) or not isinstance(reasons, dict):
        raise ValueError("judge 输出 tags/reasons 结构非法")

    tag_vocab = {d["key"]: set(d["defect_tags"]) for d in rubric_v3.DIMENSIONS}
    for key in rubric_v3.DIMENSION_KEYS:
        score = scores.get(key)
        if not isinstance(score, int) or isinstance(score, bool) or not (0 <= score <= 5):
            raise ValueError(f"维度 {key} 分数非法: {score!r}")
        dim_tags = tags.get(key, [])
        if not isinstance(dim_tags, list):
            raise ValueError(f"维度 {key} tags 非法: {dim_tags!r}")
        vocab = tag_vocab[key]
        for tag in dim_tags:
            if tag not in vocab:
                raise ValueError(f"维度 {key} 输出词表外标签: {tag}")
        if score <= rubric_v3.LOW_SCORE_THRESHOLD:
            if not dim_tags:
                raise ValueError(f"维度 {key} 分数 {score} ≤{rubric_v3.LOW_SCORE_THRESHOLD} 但未挂缺陷标签")
            if not str(reasons.get(key, "")).strip():
                raise ValueError(f"维度 {key} 低分但缺理由")


def score_case(
    demand_md: str, deliveries: dict[str, str], judge_config_id: int | None = None,
) -> dict[str, Any]:
    """对一个 case 的一次生成产物评分（1 次 judge 调用 + 规则项判定）。

    judge_config_id（FR-003）：指定 judge 配置（触发时下拉选择的）；
    None=默认解析（llm.chat scope=eval，未配置降级 evolution）。

    Returns: {
      rubric_version, calibration, anchor_status,
      scores: {维度: 分}, tags: {维度: [标签]}, reasons: {维度: 理由},
      overall: 有效维度均分（score>0 参与）,
      rule_delivery: {passed, problems},
    }
    Raises: 解析/校验失败（调用方按 FR-002 失败语义重试 1 次）。
    """
    messages = [
        {"role": "system", "content": rubric_v3.build_judge_system_prompt()},
        {"role": "user", "content": rubric_v3.build_judge_user_prompt(demand_md, deliveries)},
    ]
    raw = llm.chat(
        messages,
        temperature=0.0,
        timeout=120.0,
        phase="benchmark_score",
        scope="eval",
        config_id=judge_config_id,
    )
    judgement = _parse_response(raw)
    _validate_judgement(judgement)

    scores = {key: judgement["scores"][key] for key in rubric_v3.DIMENSION_KEYS}
    valid = [v for v in scores.values() if v > 0]
    overall = round(sum(valid) / len(valid), 2) if valid else 0.0
    return {
        "rubric_version": rubric_v3.RUBRIC_VERSION,
        "calibration": rubric_v3.CALIBRATION_STATUS,
        "anchor_status": rubric_v3.ANCHOR_DRAFT_STATUS,
        "scores": scores,
        "tags": {k: v for k, v in (judgement.get("tags") or {}).items() if v},
        "reasons": judgement.get("reasons") or {},
        "overall": overall,
        "rule_delivery": check_delivery_complete(deliveries),
    }


__all__ = [
    "load_outline_deliveries",
    "check_delivery_complete",
    "score_case",
]
