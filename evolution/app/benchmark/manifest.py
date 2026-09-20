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

# 账本版本列表查询预算（下拉数据源 + runner 版本解析共用）
_VERSIONS_TIMEOUT_S = 5.0


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


def fetch_platform_versions() -> dict[str, Any]:
    """拉 Platform 账本版本列表（GET /api/versions）。

    Phase A 后 registry.json 发版写线退役、内容冻结，账本是版本号的
    唯一活跃数据源——版本下拉与 runner 的版本→commit 解析都以此为准。

    Returns:
        {"items": [{version, commit, note, created_at}...]（版本号倒序）,
         "production_version": int | None}

    Raises:
        RuntimeError: Platform 不可达 / 非 200 / 响应畸形。
        与 fetch_platform_binding 的 fail-static 不同：调用方没有可降级的
        旧数据源，拿不到账本必须报错——静默退回冻结 registry 会让评测
        跑在错误的 harness 版本上（宁拒勿错）。
    """
    url = f"{settings.platform_url.rstrip('/')}/api/versions"
    try:
        resp = httpx.get(url, timeout=_VERSIONS_TIMEOUT_S)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Platform 账本不可达：{exc}") from exc
    if resp.status_code != 200:
        raise RuntimeError(f"Platform 账本查询异常：HTTP {resp.status_code}")
    try:
        data = resp.json()
        items = data["items"]
        if not isinstance(items, list) or not all(
            isinstance(v, dict) and isinstance(v.get("version"), int)
            and isinstance(v.get("commit"), str)
            for v in items
        ):
            raise ValueError(f"账本版本列表畸形: {data!r}")
        production = data.get("production_version")
        if production is not None and not isinstance(production, int):
            raise ValueError(f"production_version 畸形: {production!r}")
    except (KeyError, ValueError) as exc:
        raise RuntimeError(f"Platform 账本响应解析失败：{exc}") from exc
    return data


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


def resolve_judge_config(config_id: int | None = None) -> dict[str, Any]:
    """解析 judge 配置（REQ-20260920-104714/FR-003）。

    config_id 有值：用该条 llm_configs 配置（触发时下拉选择，FR-003）；
      仅接受 eval / evolution scope（DEC-010 判评分离——executor 生产模型
      不得作 judge，UI 下拉不提供，API 层同样拒绝）；配置不存在 / 缺 key /
      base_url / model 任一项 → 按 unconfigured 返回（scope="selected"，
      调用方据 config_id 给出可定位的错误信息）。
    config_id 为 None：eval scope 优先，未配置降级 evolution（DEC-012）。

    Returns: {scope, base_url, model, fingerprint, degraded}
    DB 未初始化 / 未配置 master key 等环境问题返回 unconfigured（不 raise）。
    """
    if config_id is not None:
        try:
            safe = db.LlmConfigsRepository.get_safe_by_id(config_id)
            config = db.LlmConfigsRepository.get_decrypted(config_id)
        except Exception:
            logger.warning("judge 配置 #%s 读取失败（DB 未初始化等）", config_id, exc_info=True)
            safe, config = None, None
        if (
            safe is None
            or safe.get("scope") not in ("eval", "evolution")  # DEC-010 判评分离
            or config is None or not config[0] or not config[1] or not config[2]
        ):
            return {
                "scope": "selected", "base_url": "", "model": "",
                "fingerprint": "unconfigured", "degraded": False,
            }
        _api_key, base_url, model = config
        return {
            "scope": "selected",
            "base_url": base_url,
            "model": model,
            "fingerprint": _sha16(f"{base_url.rstrip('/')}|{model}|t={JUDGE_TEMPERATURE}"),
            "degraded": False,
        }
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


def same_family_as_executor(model: str) -> bool:
    """judge 候选模型与 executor 被测模型是否同家族（FR-003/DEC-010）。

    同家族 → 界面黄条告警的数据源（自我偏好风险，arXiv:2502.01534）。
    executor 未配置时返回 False（无从比较，不告警）。
    """
    if not model:
        return False
    executor_cfg = db.LlmConfigsRepository.get_active("executor")
    if not executor_cfg or not executor_cfg[2]:
        return False
    return _model_family(model) == _model_family(executor_cfg[2])


def judge_same_family_warning() -> str | None:
    """judge 与 executor 被测模型同家族时返回告警文案（DEC-012，仅告警不阻断）。"""
    judge = resolve_judge_config()
    if not judge["model"]:
        return None
    if same_family_as_executor(judge["model"]):
        return (
            f"判评分离告警：judge 模型 {judge['model']} 与 executor 被测模型 "
            f"同家族，评测存在自我偏好风险（arXiv:2502.01534）"
        )
    return None


__all__ = [
    "JUDGE_TEMPERATURE",
    "UNBOUND",
    "fetch_platform_binding",
    "fetch_platform_versions",
    "llm_snapshot_fingerprint",
    "binding_manifest_fingerprint",
    "resolve_judge_config",
    "same_family_as_executor",
    "judge_same_family_warning",
]
