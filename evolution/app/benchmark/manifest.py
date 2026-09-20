"""评测运行装配指纹（REQ-20260919-172934 / DEC-015）。

**Platform 对齐版（演进钩子兑现）**：Manifest 机制已上收到平台控制面
（Platform 服务 7790，Run↔Manifest 绑定账本）。评测侧从「自采指纹」改为
「引用 Platform 绑定」——每个评测 run 的 trace 在 executor 侧被签发绑定
（ab_run 补签，run_purpose=optimization），跑分完成后按 trace_id 查回：

  binding = { manifest_id, harness_commit, llm_config: {model, base_url, ...} }

评测的 Manifest 指纹 = Platform manifest_id + harness commit + LLM 快照，
即「这次 run 实际装配了什么」的账本事实，而非评测侧自行拼装的推断。

fail-static 语义：Platform 不可达或绑定缺失时标记 unbound（不降级自采、
不猜测）——身份未知的行保留分数，但版本对比按指纹校验拒绝其所在批次
（宁缺毋谎，与 DEC-015「绑定必须指向明确版本」一致）。

judge 指纹不变：评分侧配置（llm_configs，eval scope 优先）不在 Platform
治理范围，仍由评测侧自采。
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

import app.core.db as db
from app.core.settings import settings

logger = logging.getLogger("evolution.benchmark.manifest")

# judge 评分温度（与 scorer 调用 llm.chat 的 temperature 一致，参与指纹）
JUDGE_TEMPERATURE = 0.0

# 绑定查询的 unbound 标记值（写入 manifest_fp，对比校验视为身份未知）
UNBOUND = "unbound"

# Platform 内网直连，查询预算收紧
_BINDING_TIMEOUT_S = 5.0


def _sha16(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ── Platform 绑定查询（Manifest 引用源）─────────────────────


def fetch_platform_binding(trace_id: str) -> dict[str, Any] | None:
    """按 trace_id 查 Platform Run 绑定（GET /api/bindings/{trace_id}）。

    Returns: BindingRecord dict；查无（404）/不可达/解析失败返回 None（fail-static，
    调用方记 unbound，不阻塞评分）。
    """
    url = f"{settings.platform_url.rstrip('/')}/api/bindings/{trace_id}"
    try:
        resp = httpx.get(url, timeout=_BINDING_TIMEOUT_S)
    except httpx.HTTPError as exc:
        logger.warning("Platform 绑定查询不可达 trace=%s: %s", trace_id, exc)
        return None
    if resp.status_code == 404:
        logger.warning("Platform 无此 Run 绑定 trace=%s（签发失败或未补签）", trace_id)
        return None
    if resp.status_code != 200:
        logger.warning("Platform 绑定查询异常 trace=%s: HTTP %s", trace_id, resp.status_code)
        return None
    try:
        record = resp.json()
        if not isinstance(record, dict) or "manifest_id" not in record:
            raise ValueError(f"绑定记录缺 manifest_id: {record!r}")
        return record
    except ValueError as exc:
        logger.warning("Platform 绑定记录解析失败 trace=%s: %s", trace_id, exc)
        return None


def llm_snapshot_fingerprint(llm_config: dict[str, Any] | None) -> str | None:
    """绑定上 LLM 快照的指纹（model + base_url，不含凭据引用之外的字段）。"""
    if not llm_config:
        return None
    model = llm_config.get("model") or ""
    base_url = (llm_config.get("base_url") or "").rstrip("/")
    if not model:
        return None
    return _sha16(f"{base_url}|{model}")


def binding_manifest_fingerprint(binding: dict[str, Any]) -> str:
    """评测 Manifest 指纹 = Platform manifest_id + harness commit + LLM 快照。"""
    llm_fp = llm_snapshot_fingerprint(binding.get("llm_config"))
    return _sha16(
        f"mid={binding.get('manifest_id')}|commit={binding.get('harness_commit')}"
        f"|llm={llm_fp}"
    )


# ── judge 评分配置指纹（评分侧自采，不随 Platform 迁移）──────


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


__all__ = [
    "JUDGE_TEMPERATURE",
    "UNBOUND",
    "fetch_platform_binding",
    "llm_snapshot_fingerprint",
    "binding_manifest_fingerprint",
    "resolve_judge_config",
    "judge_same_family_warning",
]
