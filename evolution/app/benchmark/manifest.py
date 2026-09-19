"""评测运行装配指纹（REQ-20260919-172934 / DEC-015，TD-005）。

Manifest = harness commit + 被测模型实际身份。跑分记录另绑定评分配置
（rubric 版本 + judge 模型指纹）。四类指纹任一变更，历史基线失效。

设计要点：
- judge 指纹按「eval scope 优先、未配置降级 evolution」解析（DEC-012 判评分离），
  指纹内容 = base_url + model + 评分温度，不含 API key（凭据不入指纹）。
- 被测模型指纹从 trace 的 llm_start 事件提取实际 model_name 集合——
  「实际采用了什么」而非「声明配置了什么」（Manifest 哲学：git commit 只标识
  仓库内容，不说明本次运行实际用的模型）。
- 字段结构与未来 Platform 级 Manifest 对齐（DEC-015 演进钩子）：届时装配时冻结
  Manifest 并绑定 Run，评测从「自采指纹」改为「引用 manifest_id」。
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import app.core.db as db

logger = logging.getLogger("evolution.benchmark.manifest")

# judge 评分温度（与 scorer 调用 llm.chat 的 temperature 一致，参与指纹）
JUDGE_TEMPERATURE = 0.0


def _sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ── judge 评分配置指纹 ───────────────────────────────────────


def resolve_judge_config() -> dict[str, Any]:
    """解析 judge 配置：eval scope 优先，未配置降级 evolution（DEC-012）。

    Returns: {scope, base_url, model, fingerprint, degraded}
    DB 未初始化 / 未配置 master key 等环境问题返回 unconfigured（不 raise）。
    """
    try:
        config = db.LlmConfigsRepository.get_active("eval")
        degraded = False
        if config is None:
            config = db.LlmConfigsRepository.get_active("evolution")
            degraded = True
        if config is None:
            raise _Unconfigured()
    except Exception:
        logger.warning("judge 配置解析失败（DB 未初始化或未配置），按 unconfigured 处理", exc_info=True)
        return {
            "scope": "none", "base_url": "", "model": "",
            "fingerprint": "unconfigured", "degraded": True,
        }
    _api_key, base_url, model = config
    return {
        "scope": "evolution" if degraded else "eval",
        "base_url": base_url,
        "model": model,
        "fingerprint": _sha16(f"{base_url.rstrip('/')}|{model}|t={JUDGE_TEMPERATURE}"),
        "degraded": degraded,
    }


class _Unconfigured(RuntimeError):
    pass


def _model_family(model: str) -> str:
    """按模型名前缀粗判家族（与 model_factory._model_family 同口径）。"""
    lower = model.lower()
    for prefix in ("deepseek", "glm", "qwen", "gpt", "claude", "gemini", "kimi", "moonshot", "llama", "doubao"):
        if prefix in lower:
            return prefix
    return lower.split("-")[0] if lower else "unknown"


def judge_same_family_warning() -> str | None:
    """judge 与 executor 被测模型同家族时返回告警文案（DEC-012，仅告警不阻断）。"""
    judge = resolve_judge_config()
    if not judge["model"]:
        return None
    executor_cfg = db.LlmConfigsRepository.get_active("executor")
    if not executor_cfg:
        return None
    _key, _base, executor_model = executor_cfg
    if not executor_model:
        return None
    if _model_family(judge["model"]) == _model_family(executor_model):
        return (
            f"判评分离告警：judge 模型 {judge['model']} 与 executor 被测模型 "
            f"{executor_model} 同家族，评测存在自我偏好风险（arXiv:2502.01534）"
        )
    return None


# ── 被测模型指纹（从 trace 事件提取实际使用）─────────────────


def tested_model_names(trace_id: str) -> list[str]:
    """从 trace 的 llm_start 事件提取实际使用的 model_name 集合（去重排序）。"""
    rows = db.query_all(
        "SELECT payload_json FROM event_payloads WHERE trace_id=? AND type='llm_start'",
        (trace_id,),
    )
    names: set[str] = set()
    for row in rows:
        try:
            names.add(json.loads(row["payload_json"]).get("model_name") or "")
        except (json.JSONDecodeError, TypeError):
            continue
    names.discard("")
    return sorted(names)


def tested_model_fingerprint(trace_id: str) -> str:
    """被测模型指纹：实际使用 model_name 集合的稳定摘要。"""
    return _sha16("|".join(tested_model_names(trace_id)))


# ── Manifest 指纹（harness + 被测模型）──────────────────────


def manifest_fingerprint(harness_commit: str, tested_model_fp: str) -> str:
    """运行装配 Manifest 指纹 = harness commit + 被测模型指纹（DEC-015）。"""
    return _sha16(f"{harness_commit}|model={tested_model_fp}")


__all__ = [
    "JUDGE_TEMPERATURE",
    "resolve_judge_config",
    "judge_same_family_warning",
    "tested_model_names",
    "tested_model_fingerprint",
    "manifest_fingerprint",
]
