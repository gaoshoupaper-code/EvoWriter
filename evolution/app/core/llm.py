"""LLM 调用：调 OpenAI 兼容的 chat completions API（deepseek / openai 等）。

故意不依赖 langchain/openai-sdk，用 httpx 直调兼容协议，保持 evolution 独立轻量。

桌面化改造（2026-07-07）：配置不再从 settings.judge_* 读，改从 llm_config 表读
（桌面端填 → HTTP → evolution 加密存）。见 app/core/db.py 的 LlmConfigRepository。

统一观测边界（DEC-001）：直接 httpx 调用绕过了 DeepAgent/TraceMiddleware，证据编纂
和内容评分的耗时阶段因此不可观测。chat 现接受可选 trace 观察者，在不引入 langchain
依赖的前提下，让确定性流水线把每次模型调用作为 llm_start/llm_end/llm_error span 写进
同一 Trace。观察者只在调用方显式传入时生效——Agent 路径继续由 TraceMiddleware 覆盖。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Protocol, runtime_checkable

import httpx

from app.core import db


@runtime_checkable
class LlmCallObserver(Protocol):
    """直接 llm.chat 调用的统一观测协议（DEC-001）。

    调用方（证据编纂 / 内容评分）把一个实现本协议的对象通过 trace= 传入 chat，
    chat 在调用前后回调，让确定性流水线的每次模型调用进入同一 Trace。

    不依赖 TraceRecorder 具体类型，保持 llm 模块零 langchain/trace 依赖。
    """

    def on_llm_start(self, *, phase: str, model: str, messages: list[dict[str, str]]) -> None: ...

    def on_llm_end(
        self, *, phase: str, model: str, duration_ms: float, output: str,
    ) -> None: ...

    def on_llm_error(
        self, *, phase: str, model: str, duration_ms: float, error: BaseException,
    ) -> None: ...


def judge_enabled() -> bool:
    """LLM-judge 是否可用（evolution scope 已配置 api_key + base_url + model）。"""
    return db.LlmConfigsRepository.get_active("evolution") is not None


def _get_config(scope: str = "evolution") -> tuple[str, str, str]:
    """读取指定 scope 的 LLM 配置 (api_key, base_url, model)。未配置抛 RuntimeError。

    scope 扩展（REQ-20260919-172934 / DEC-012）：'eval'（评测 judge）未配置时
    降级 evolution scope 并告警——判评分离的降级路径，与 model_factory 同语义。
    """
    config = db.LlmConfigsRepository.get_active(scope)
    if config is None and scope != "evolution":
        logging.getLogger("evolution.core.llm").warning(
            "scope=%s 未配置 LLM，降级使用 evolution scope 配置", scope
        )
        config = db.LlmConfigsRepository.get_active("evolution")
    if config is None:
        raise RuntimeError(
            "LLM 未配置。请在桌面端「进化端模型」页填写大模型 API（base_url / api_key / model）。",
        )
    return config


def chat(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.0,
    timeout: float = 60.0,
    trace: LlmCallObserver | None = None,
    phase: str = "llm",
    scope: str = "evolution",
    config_id: int | None = None,
) -> str:
    """调一次 chat completion，返回 assistant 文本。

    messages: OpenAI 格式 [{"role":"system","content":...}, {"role":"user","content":...}]
    兼容 deepseek / openai / 任何 OpenAI 兼容端点。

    trace/phase（DEC-001）：确定性流水线（证据编纂、内容评分）通过 trace 传入观测者，
    chat 回调它把本次调用记录为 llm span。Agent 路径不传 trace，仍由 TraceMiddleware 覆盖。
    scope（DEC-012）：默认 evolution；评测 judge 传 'eval'（未配置降级 evolution）。
    config_id（REQ-20260920-104714/FR-003）：显式指定用哪条 llm_configs 配置，
    优先于 scope 解析——评测 judge 触发时下拉选择的配置经此传入；未配置或被删
    时抛 RuntimeError（调用方按失败语义处理）。
    """
    if config_id is not None:
        config = db.LlmConfigsRepository.get_decrypted(config_id)
        if config is None:
            raise RuntimeError(f"指定的 LLM 配置 #{config_id} 不存在或不可用")
        api_key, base_url_raw, model_raw = config
    else:
        api_key, base_url_raw, model_raw = _get_config(scope)
    base_url = base_url_raw.rstrip("/")
    url = f"{base_url}/chat/completions"
    # model 可能是 "openai:gpt-4o-mini" 或 "gpt-4o-mini"，去掉 provider 前缀
    model = model_raw.split(":", 1)[-1]

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    started = time.perf_counter()
    if trace is not None:
        trace.on_llm_start(phase=phase, model=model, messages=messages)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except BaseException as exc:
        if trace is not None:
            trace.on_llm_error(
                phase=phase, model=model,
                duration_ms=_elapsed_ms(started), error=exc,
            )
        raise
    # OpenAI 兼容格式：choices[0].message.content
    content = data["choices"][0]["message"]["content"]
    if trace is not None:
        trace.on_llm_end(
            phase=phase, model=model,
            duration_ms=_elapsed_ms(started), output=content,
        )
    return content


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 1)


__all__ = ["chat", "judge_enabled", "LlmCallObserver"]
