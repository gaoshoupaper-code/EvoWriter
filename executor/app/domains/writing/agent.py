from __future__ import annotations

import asyncio
import json
import logging
from contextlib import nullcontext

from pathlib import Path
from typing import Any, AsyncIterator

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

from contracts.platform import BindingRecord

from app.platform.agent.base_service import BaseAgentService
from app.platform.streaming import ExtraTask, run_agent_stream
from app.domains.writing.events import WritingEventSink
from app.domains.writing.conversation_log import ConversationLog, ConversationLogError
from app.domains.writing.run_gate import get_conversation_gate
from app.domains.writing.expert_agent.services.storyline_graph import generate_storyline_graph
from app.domains.writing.models import build_writer_model
from app.platform.agent.middleware import (
    TraceCallbackHandler,
    TraceMiddleware,
)
from app.platform.agent.loader import load_current_package
from app.platform.memory import get_memory_backend
from app.platform.core.settings import Settings
from app.domains.writing.styling.store import CreateTypeStore
from app.platform.trace import TraceRecorder
from app.schemas.screenplay import (
    ScreenplayGenerateRequest,
    ScreenplayGenerateResponse,
    ThreadSummary,
)

# SSE 心跳间隔已收敛到 platform.streaming.DEFAULT_HEARTBEAT_INTERVAL（PR-07a）。
# 排队期心跳间隔（FR-006）：远小于客户端 45s 断流阈值，同帧格式注释帧。
_QUEUE_HEARTBEAT_S = 15.0

logger = logging.getLogger("writer.meta_agent")


def _safe_version(pkg: str) -> str | None:
    """读取已安装包版本，缺失时返回 None（CON-003：Trace 快照可追溯 SDK 版本）。"""
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version(pkg)
    except PackageNotFoundError:
        return None
    except Exception:
        return None


def _build_tool_replay_policy(trace_recorder, trace_id: str | None):
    """构建带 Trace 观测回调的 TaskReplayPolicy（CON-005 平台级 task 防重放）。

    命中防重放（task 被通用恢复重试）时，写一条 middleware_intervention 事件，
    让"已阻止整任务重放"在 Trace 时间线可见（FR-003 可观测）。
    """
    from app.domains.writing.task_replay_policy import TaskReplayPolicy

    recorder = trace_recorder
    tid = trace_id

    def _on_blocked(tool_name: str, policy_version: str, exc: BaseException) -> None:
        if recorder is None or not tid:
            return
        record_intervention = getattr(recorder, "record_intervention", None)
        if not callable(record_intervention):
            return
        try:
            record_intervention(
                tid,
                "meta-agent",
                action="blocked_task_replay",
                hook="wrap_tool_call",
                affected_fields=["control_flow"],
                reason=f"{type(exc).__name__}:{tool_name}",
            )
        except Exception:  # noqa: BLE001 —— 观测失败不得阻断执行
            logger.debug("task 防重放 intervention 记录失败", exc_info=True)

    return TaskReplayPolicy(on_replay_blocked=_on_blocked)


def _make_retry_runner_factory():
    """构造模型重试运行器工厂（FR-003/EVD-006 可观测）。

    返回一个工厂，harness assemble 把它传给 TraceMiddleware。TraceMiddleware
    调用 ``runner(invoke_async, on_attempt, on_backoff)`` 执行带预算的模型调用。
    重试实现（WriterRetryController）定义在本领域层，平台层只消费工厂、不 import，
    保持 platform 不依赖 domains 的分层铁律。
    """

    def _factory():
        from app.domains.writing.retry import MAX_TRANSMIT_ATTEMPTS, RetryBudget, WriterRetryController

        async def _runner(invoke_async, on_attempt, on_backoff):
            budget = RetryBudget(
                max_attempts=MAX_TRANSMIT_ATTEMPTS,
                backoff_seconds=1.0,
                on_attempt_complete=lambda outcome: (
                    None if outcome.success
                    else on_attempt(outcome.attempt_number, MAX_TRANSMIT_ATTEMPTS, outcome.error_class or "unknown")
                ),
                on_backoff=lambda _aid, attempt, delay: on_backoff(attempt, attempt + 1, delay),
            )
            # 把 invoke 包一层：跟踪本次 attempt 是否已收到任意响应增量；若已收到增量
            # 后抛错，给异常打 _writer_partial_response 标记，让控制器按 DEC-006 禁止重放。
            tracked = _track_partial_response(invoke_async)
            return await WriterRetryController(budget).acall(tracked.invoke, is_partial=tracked.is_partial)

        return _runner

    return _factory


def _track_partial_response(invoke_async):
    """包装 invoke，检测"已收到响应增量后中断"的部分响应（DEC-006/EDGE-005）。

    invoke 返回完整响应对象；若返回成功即视为本次 attempt 收到了完整响应（非部分）。
    真正的部分响应（流式过程中已产出 chunk 后断流）会以异常抛出——此时无法在不侵入
    LangChain 流式内部的前提下可靠捕获 chunk 计数，故采用保守启发：若异常携带的
    response body / api response 已含 choices 或 usage 字段，视为已部分到达，禁止重放。
    宁可对不确定的情况保守判为部分响应（不重放），也不冒险重复副作用/计费（RSK-002/006）。
    """
    seen_increment = False

    async def _wrapped():
        nonlocal seen_increment
        result = await invoke_async()
        # 成功返回 = 收到了完整响应（非部分失败）。
        seen_increment = True
        return result

    def _is_partial(exc: BaseException) -> bool:
        if seen_increment:
            return False  # 成功返回过的不在此讨论
        # 检查异常是否携带已部分到达的响应体（openai 流式错误常带 response）。
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                body = None
                if hasattr(response, "json"):
                    try:
                        body = response.json()
                    except Exception:  # noqa: BLE001
                        body = None
                if body is None:
                    text = getattr(response, "text", None)
                    if isinstance(text, str) and text:
                        import json as _json
                        try:
                            body = _json.loads(text)
                        except Exception:  # noqa: BLE001
                            body = None
                if isinstance(body, dict) and ("choices" in body or "usage" in body):
                    return True
            except Exception:  # noqa: BLE001
                pass
        return False

    from types import SimpleNamespace

    return SimpleNamespace(invoke=_wrapped, is_partial=_is_partial)

    return _factory


def _get_memory_recall_cls(pkg=None):
    """从 harness 包动态加载 MemoryRecallMiddleware 类。

    MemoryRecallMiddleware 定义在 harness 包的 middleware/ 下（可进化要素），
    executor 运行时不硬依赖它——通过包对象动态获取。生产路径默认用
    ``load_current_package()``；A/B/单次测试路径传入自身已加载的候选包
    （不同 source_root 的版本），确保装配的是该候选版本的中间件（FR-003 版本隔离）。
    harness 包尚未实现该 middleware 时返回 None（向后兼容，走全量注入）。
    """
    try:
        import importlib

        target = pkg or load_current_package()
        mw_module = importlib.import_module(f"{target.__name__}.middleware.memory_recall_middleware")
        return getattr(mw_module, "MemoryRecallMiddleware", None)
    except Exception as e:
        logger.debug("MemoryRecallMiddleware 加载失败（走全量注入）：%s", e)
        return None


def _make_artifact_snapshot_cb(recorder, trace_id: str | None):
    """构造产物修订快照回调（第二期证据采集，2026-07）。

    ArtifactSnapshotMiddleware 写盘成功后调用，传入已回读快照 dict。
    回调创建 ArtifactRevision 事实；正文由 recorder 经 PayloadGate 引用化保存。
    recorder 或 trace_id 为 None 时返回 None（不采集）。
    """
    if recorder is None or not trace_id:
        return None

    def _callback(snapshot_data: dict) -> None:
        try:
            recorder.record_artifact_revision(
                trace_id,
                snapshot_data.get("agent_name", "unknown"),
                file_path=snapshot_data["file_path"],
                content=snapshot_data.get("content", ""),
                tool_name=snapshot_data.get("tool"),
                tool_call_id=snapshot_data.get("tool_call_id"),
                content_hash=snapshot_data.get("fingerprint"),
            )
        except Exception:
            pass  # 快照失败不影响写作流程

    return _callback


def _emit_contract_snapshot(recorder, trace_id: str, payload, thread, run_purpose: str):
    """新 trace 启动时冻结任务契约快照（第二期证据采集，2026-07）。

    从 payload + thread 提取八类契约字段，emit 一条 run_meta 事件。
    拿不到的字段标 missing，不阻塞写作流程。
    """
    try:
        # 尝试读 demand.md（契约核心，可能尚未生成——interview 阶段才产出）
        workspace_path = Path(thread.workspace_path)
        demand_path = workspace_path / "demand.md"
        demand_md = None
        try:
            if demand_path.exists():
                text = demand_path.read_text(encoding="utf-8")
                demand_md = text[:4000] if text.strip() else None
        except (OSError, UnicodeDecodeError):
            pass

        # 从 payload 提取原始用户目标
        primary_text = ""
        for attr in ("prompt", "content", "text", "premise"):
            val = getattr(payload, attr, None)
            if val:
                primary_text = str(val)
                break

        contract = {
            "user_goal": primary_text or None,
            "task_type": "screenplay.generate.stream",
            "run_purpose": run_purpose,
            "endpoint": "screenplay.generate.stream",
            "thread_id": thread.thread_id,
            "workspace_id": thread.workspace_id,
            "session_name": getattr(thread, "session_name", None),
            "demand_md": demand_md,
            "demand_available": demand_md is not None,
            "missing": [],
        }
        # 标注缺失字段
        if not demand_md:
            contract["missing"].append("demand.md 尚未生成（interview 阶段才产出，契约快照在任务开始时冻结）")
        for field in ("promised_artifacts", "hard_constraints", "style_preferences",
                      "scope", "ambiguities", "input_references"):
            contract["missing"].append(f"{field}：需语义层从 demand.md 提取")

        recorder.append_event(trace_id, {
            "type": "run_meta",
            "source": "system",
            "input": {"contract_snapshot": contract},
        })
    except Exception:
        pass  # 契约快照失败不影响写作流程
    """构造任务契约快照回调（第二期证据采集，2026-07）。

    任务启动时调用一次，传入契约八类字段。
    回调 emit 一条 run_meta 事件（input.contract_snapshot），供证据编译器重建任务契约。
    recorder 或 trace_id 为 None 时返回 None（不采集）。
    """
    if recorder is None or not trace_id:
        return None

    def _callback(contract_data: dict) -> None:
        try:
            recorder.append_event(trace_id, {
                "type": "run_meta",
                "source": "system",
                "input": {"contract_snapshot": contract_data},
            })
        except Exception:
            pass  # 契约快照失败不影响写作流程

    return _callback


def build_llm_snapshot():
    """从 llm_config loader 构造脱敏 LLM 快照（contracts 红线：不含明文 key）。

    模块级函数：generate_stream 主链与 A/B 子进程任务的绑定签发共用同一构造，
    保证两类 Run 在 Platform 账本中同构（FR-007）。
    loader 未配置/降级返回 None（快照缺省，绑定照签——llm_config 非必填）。
    """
    from app.platform.llm_config.loader import get_active_llm_config
    from contracts.platform import LlmConfigSnapshot

    cfg = get_active_llm_config()
    if cfg is None:
        return None
    return LlmConfigSnapshot(
        model=cfg.model, base_url=cfg.base_url,
        api_key_ref=None,  # Phase A：无凭据引用体系，明文 key 绝不进快照
        source="evolution",
    )


class MetaAgentService(BaseAgentService):
    def __init__(self, settings: Settings, workspace_root: Path, trace_recorder: TraceRecorder, style_store: CreateTypeStore, checkpointer: BaseCheckpointSaver) -> None:
        # 复用 BaseAgentService 的通用初始化（settings/workspace_root/trace_recorder/checkpointer）
        super().__init__(settings, workspace_root, trace_recorder, checkpointer)
        self.style_store = style_store

    # ── 模型构建（PR-12：从基类下沉，消除 platform→domains 反向依赖）─────
    def _build_model_default(self):
        """写作领域的默认模型（无 owner/key 时）。"""
        return build_writer_model(self.settings)

    def _build_model_with_key(self, key: str, base_url: str | None, model_name: str | None):
        """按 owner 的 key 构建写作模型（多用户隔离 D9）。"""
        return build_writer_model(
            self.settings, api_key=key, base_url=base_url, model_name_override=model_name,
        )

    def _resolve_style_for_subagent(self, workspace_id: str, style_key: str, owner_id: str | None = None) -> str | None:
        """从激活风格中提取指定子代理对应的风格文本（SUFFIX）。

        Args:
            workspace_id: 工作区 ID
            style_key:    风格字段名（storybuilding_style / detail_outline_style / writing_style）
            owner_id:     所有者 ID（多用户隔离，None 回退管理员兜底）

        Returns:
            风格 SUFFIX 文本，无激活风格或该字段为空时返回 None
        """
        style_id = self.style_store.get_active_style_id(owner_id or "", workspace_id)
        if not style_id:
            return None
        style = self.style_store.get_style(owner_id or "", style_id)
        if not style:
            return None
        text = style.get(style_key, "")
        return text.strip() if text else None

    def _resolve_meta_style(self, workspace_id: str, owner_id: str | None = None) -> str | None:
        style_id = self.style_store.get_active_style_id(owner_id or "", workspace_id)
        if not style_id:
            return None
        style = self.style_store.get_style(owner_id or "", style_id)
        if not style:
            return None
        return style.get("meta_style") or None

    def _compose_user_writing_skills(
        self, workspace_id: str | None, owner_id: str | None, workspace_path: Path,
    ) -> str | None:
        """组装当前段用户写作技能提示词段（REQ-20261025-205943 FR-003）。

        服务端硬闸：仅五段制工作区且当前段 ∈ {outline, volume, chapter,
        text}（DEC-001 四段开放）时，注入「归属该段」的启用技能（DEC-002
        单一阶段归属——他段技能即使已启用也不注入）；题材段/legacy 一律
        None。注入文本自带两条声明（DEC-008）：效力低于「表达铁律」＋
        不得突破产物结构约定。段判定与切段接口同源（state.json 人工段
        指针）。用户内容只走运行时 DB 读取，不进 harness 发版制品。
        """
        if not workspace_id or not owner_id:
            return None
        from app.domains.writing.pipeline import classify_workspace, current_stage

        if classify_workspace(workspace_path) != "five":
            return None
        from app.platform.core.db import WritingSkillRepository, get_database

        stage = current_stage(workspace_path)
        if stage not in WritingSkillRepository.STAGE_VALUES:
            return None
        skills = [
            sk for sk in WritingSkillRepository(get_database())
            .list_enabled_for_workspace(workspace_id, owner_id)
            if sk["stage"] == stage
        ]
        if not skills:
            return None
        parts = [
            "## 作者启用的写作技能（作者本人设定）",
            "",
            "以下技能由作者亲自启用，仅作题材与场景层面的补充指导；"
            "效力低于上方「表达铁律」，与表达铁律冲突时一律以表达铁律为准；"
            "技能不得突破当前段规程约定的产物结构与流程，冲突时一律以段规程为准：",
            "",
        ]
        for sk in skills:
            parts.append(f"### {sk['name']}——{sk['description']}")
            parts.append("")
            parts.append(sk["content"].strip())
            parts.append("")
        return "\n".join(parts).strip()

    def _apply_user_writing_skills(
        self, *, styles: dict[str, str] | None, agent_name: str | None,
        workspace_id: str | None, owner_id: str | None, workspace_path: Path,
    ) -> dict[str, str] | None:
        """把用户写作技能追加进 styles["author"]（段规程之后）。失败静默跳过。

        仅 author 装配路径调用；查询失败不阻塞创作主流程（FR-003 失败语义）。
        """
        if agent_name != "author" or not workspace_id or not owner_id:
            return styles
        try:
            suffix = self._compose_user_writing_skills(workspace_id, owner_id, workspace_path)
            if not suffix:
                return styles
            base = (styles or {}).get("author") or ""
            merged = f"{base}\n\n{suffix}" if base else suffix
            return {**(styles or {}), "author": merged}
        except Exception:  # noqa: BLE001 — 注入失败退回无技能装配，不阻塞
            logger.warning("用户写作技能注入失败，跳过", exc_info=True)
            return styles

    def _agent_for_workspace(self, workspace_path: Path, trace_id: str | None = None, workspace_id: str | None = None, *, model=None, checkpointer=None, owner_id: str | None = None, strict_evidence: bool = False, llm_override=None, binding: BindingRecord | None = None, agent_name: str | None = None):
        # 多用户隔离（T2.4/T2.5）：model 用用户解密 key 构建，
        # checkpointer 用用户的分库 saver。两者外部注入，缺省回退全局（管理员兜底）。
        # Phase 7 包化重构：装配逻辑从 evolution 拉 manifest 改为同进程 import Agent 包。
        # 包自带 assemble(ctx)，执行端只构建 RuntimeContext 传入（D1=B / D8=X）。
        # Phase A（FR-004）：llm_override/binding 由 generate_stream 的 Run 绑定链路
        # 传入——Run 级模型锁定 + 绑定摘要进 trace 快照。
        # 单主控三步管线（REQ-20261015-161200 FR-002）：agent_name 按名装配
        #（writer）；None = 首个 main（legacy 只读模式兼容）。
        return self._assemble_via_package(
            workspace_path, trace_id, workspace_id,
            model=model, checkpointer=checkpointer, owner_id=owner_id,
            strict_evidence=strict_evidence,
            llm_override=llm_override, binding=binding,
            agent_name=agent_name,
        )

    def _assemble_via_package(
        self,
        workspace_path: Path,
        trace_id: str | None = None,
        workspace_id: str | None = None,
        *,
        model=None,
        checkpointer=None,
        owner_id: str | None = None,
        strict_evidence: bool = False,
        llm_override=None,
        binding: BindingRecord | None = None,
        agent_name: str | None = None,
    ):
        """Phase 7：经 Agent 包装配（替代 _assemble_via_manifest）。

        import Agent 包 → 构建 RuntimeContext（含 styles）→ package.assemble(ctx)。
        执行端只负责构建 model/backend/checkpointer + 解析 styles，装配全在包内。

        风格注入（D2/D4）：从 styling store 解析当前 workspace 的激活风格，
        按 scope 名填充 ctx.styles（包内 assemble 消费，注入各 subagent prompt）。
        scope→字段名映射就地处写（D4 决策）：
          meta → meta_style, storybuilding → storybuilding_style,
          detail-outline → detail_outline_style, writing → writing_style。

        Phase A（FR-004）：
          - llm_override：Run 绑定的 LLM 快照——有值时 model 构建优先用快照的
            model/base_url（key 仍走受控通道现取，快照本身不含明文 key）。
          - binding：Run 绑定记录摘要，写进 run_snapshot（字段 binding），
            供事后追溯「这条 trace 当时装配的 harness commit 与 LLM 配置」。
        """
        from contracts.runtime_context import RuntimeContext
        from app.platform.agent.loader import load_current_package
        from app.platform.agent.runtime import artifact_capture_scope
        from app.domains.writing.models import build_writer_model
        from app.platform.agent.middleware import TraceMiddleware

        # FR-007（REQ-20260920-150149）：免计费——CreditsService/CreditsMiddleware
        # 不再注入（关闭不拆：platform/credits 代码与 DB 保留）。
        if model is None:
            model = build_writer_model(self.settings, llm_override=llm_override)
        if checkpointer is None:
            checkpointer = self.checkpointer
        pkg = load_current_package()

        if trace_id:
            try:
                from app.platform.agent.loader import production_checkout, production_commit
                from app.platform.agent.runtime_identity import build_runtime_identity

                harness_commit = production_commit()
                run_snapshot = build_runtime_identity(
                    harness_root=production_checkout(),
                    harness_commit=harness_commit,
                )
                run_snapshot.update({
                    "harness_version": harness_commit or "unknown",
                    "model_class": f"{model.__class__.__module__}.{model.__class__.__name__}",
                    "model_name": str(
                        getattr(model, "model_name", None)
                        or getattr(model, "model", None)
                        or model.__class__.__name__
                    ),
                    # CON-003：把决定重试/超时行为的关键配置冻结进 Trace 快照，
                    # 证明行为不依赖第三方默认值、可随镜像重建复核（RSK-004）。
                    "writer_retry": {
                        "sdk_max_retries": getattr(model, "max_retries", None),
                        "transmit_max_attempts": 2,  # WriterRetryController 预算（DEC-003）
                        "request_timeout_s": getattr(model, "request_timeout", None),
                        "openai_version": _safe_version("openai"),
                        "langchain_openai_version": _safe_version("langchain_openai"),
                    },
                })
                # FR-004：Run 绑定摘要并进快照（bound commit/degraded/manifest 级联可追溯）
                if binding is not None:
                    run_snapshot["binding"] = binding.model_dump(mode="json")
                # 三步管线（FR-002 泳道打标→主控打标）：进化端 trace 按 agent 归因
                if agent_name:
                    run_snapshot["pipeline_agent"] = agent_name
                self.trace_recorder.set_run_snapshot(trace_id, run_snapshot)
            except Exception as exc:
                self.trace_recorder._mark_capture_degraded(
                    trace_id, f"run_snapshot_failed:{type(exc).__name__}"
                )

        backend = self._backend_for_workspace(workspace_path)

        # 风格解析（D4 就地转换：scope 名 key，styling 字段名查值）
        # 无激活风格时 styles=None，包内用裸 prompt（无 suffix）。
        styles: dict[str, str] | None = None
        if workspace_id:
            style_map = {
                "meta": ("meta_style", None),  # (字段名, subagent style_key)，meta 无 subagent key
                "storybuilding": ("storybuilding_style", "storybuilding_style"),
                "detail-outline": ("detail_outline_style", "detail_outline_style"),
                "writing": ("writing_style", "writing_style"),
            }
            resolved: dict[str, str] = {}
            meta_style = self._resolve_meta_style(workspace_id, owner_id)
            if meta_style:
                resolved["meta"] = meta_style
            for scope, (_, sub_key) in style_map.items():
                if sub_key is None:
                    continue
                suffix = self._resolve_style_for_subagent(workspace_id, sub_key, owner_id)
                if suffix:
                    resolved[scope] = suffix
            styles = resolved or None

        # 五段制段规程注入（REQ-20261018-115508 DEC-002）：author 的段流程
        # 提示词按当前大段经 styles 通道注入——styles 是解释器既有的 prompt
        # suffix 注入口，复用它避免为「按段换提示词」扩清单 schema（TD：
        # styles 通道复用）。段解析用 current_stage（点名跳段优先，与
        # stage_gate/route 同口径，C3 修复）。legacy（agent_name=None）不注入。
        if agent_name == "author":
            try:
                from app.domains.writing.pipeline import classify_workspace, current_stage

                if classify_workspace(workspace_path) == "five":
                    stage_prompt_fn = getattr(pkg, "stage_prompt", None)
                    if callable(stage_prompt_fn):
                        stage_text = stage_prompt_fn(current_stage(workspace_path))
                        if stage_text:
                            styles = {**(styles or {}), "author": stage_text}
            except Exception:  # noqa: BLE001 — 注入失败退回常驻骨架，不阻塞装配
                logger.warning("段规程注入失败，退回常驻骨架", exc_info=True)

        # 用户自定义写作技能（REQ-20261023-000000 FR-003）：服务端硬闸在
        # _compose 内（仅正文段）；非正文段 styles 与现状逐字一致（AC-003）。
        styles = self._apply_user_writing_skills(
            styles=styles, agent_name=agent_name, workspace_id=workspace_id,
            owner_id=owner_id, workspace_path=workspace_path,
        )

        ctx = RuntimeContext(
            model=model,
            backend=backend,
            checkpointer=checkpointer,
            workspace_path=workspace_path,
            trace_id=trace_id,
            owner_id=owner_id,
            styles=styles,
            trace_recorder=self.trace_recorder,
            trace_middleware_cls=TraceMiddleware,  # T2：类由执行端注入，包内实例化
            # CON-005/FR-003：注入平台级 task 防重放边界。harness 的 ErrorRecovery
            # 在重试前查 should_retry；task（子 Agent 委派）恒为 False，不得重放。
            tool_replay_policy=_build_tool_replay_policy(self.trace_recorder, trace_id),
            # FR-003/EVD-006：开启模型传输层重试可观测——TraceMiddleware 用
            # 领域层注入的 retry_runner 包裹模型调用，attempt/退避写 Trace 事件。
            writer_retry_runner_factory=_make_retry_runner_factory(),
            # NWM 记忆系统：per-workspace backend（workspace_id 定位 memory.db）。
            # pool 未初始化或配置缺失时返回 None → writing 走 ContextAssembler 全量注入。
            memory_backend=get_memory_backend(f"{owner_id}_{workspace_path.name}"),
            memory_recall_middleware_cls=_get_memory_recall_cls(),  # 从 harness 包加载
            # 第二期证据采集（2026-07）：产物快照 callback。
            # 包内 _make_artifact_snapshot_callback 优先用执行端传入的 callback。
            # 任务契约快照在 generate_stream 里直接 emit（不走 callback，因为它是任务级一次性事件）。
            artifact_snapshot_callback=_make_artifact_snapshot_cb(self.trace_recorder, trace_id),
        )

        with artifact_capture_scope(
            recorder=self.trace_recorder,
            trace_id=trace_id or "",
            workspace_root=workspace_path,
            strict=strict_evidence,
        ) if trace_id else nullcontext():
            return pkg.assemble(ctx, agent_name=agent_name)

    # get_thread_checkpoint 复用 BaseAgentService 基类实现（PR-10 已提取，
    # 含 _normalize_message 规范化）。本地重复的 override + _normalize_message
    # / _map_role 已删除，消除与 base_service.py 的重复定义。

    # ── Phase A（FR-004/FR-005）：Run 绑定签发 + resume 兼容门禁 ──────

    def _build_llm_snapshot(self):
        """从 llm_config loader 构造脱敏 LLM 快照（contracts 红线：不含明文 key）。

        loader 未配置/降级返回 None（快照缺省，绑定照签——llm_config 非必填）。
        """
        return build_llm_snapshot()

    def _resolve_binding_commit(self) -> str:
        """解析 Run 绑定用的 harness commit（绑定必须指向明确的装配版本）。

        优先取已加载的生产包 commit（进程内多数 Run 命中）；进程刚启动、包尚未
        加载时调 load_current_package()（下载+装配+缓存 _current_commit）——
        同一 Run 内后续 _assemble_via_package 的 load_current_package 直接命中
        缓存，不再多一次 HTTP。都拿不到返回空串（跳过签发）。
        """
        from app.platform.agent.loader import load_current_package, production_commit

        commit = production_commit()
        if commit:
            return commit
        try:
            load_current_package()
            return production_commit()
        except Exception:  # noqa: BLE001 —— 查不到不签发，不阻塞生成
            logger.warning("Run 绑定跳过：无法确定生产 commit", exc_info=True)
            return ""

    def _issue_run_binding(self, trace_id: str, run_purpose: str):
        """create_run 成功后签发 Run 绑定（fail-static，绝不阻塞生成）。

        Returns: (llm_snapshot | None, BindingRecord | None)。
        快照同时用于本 Run 的模型锁定（generate_stream 消费）；绑定记录
        （含 degraded 本地降级记录）写进 trace run_snapshot 的 binding 字段。
        """
        snapshot = self._build_llm_snapshot()
        try:
            commit = self._resolve_binding_commit()
            if not commit:
                return snapshot, None
            from app.platform.agent.binding_client import get_binding_client

            record, _created = get_binding_client().bind(
                trace_id=trace_id,
                harness_commit=commit,
                llm_snapshot=snapshot,
                run_purpose=run_purpose,
            )
            return snapshot, record
        except Exception:  # noqa: BLE001 —— 绑定是观测/门禁辅助，失败不影响写作
            logger.warning("Run 绑定签发异常（跳过）: trace=%s", trace_id, exc_info=True)
            return snapshot, None

    def _resume_binding_snapshot(self, trace_id: str):
        """resume 续跑的 Run 从绑定记录取 LLM 快照（FR-004 版本一致续跑）。

        无绑定记录/查询失败返回 None（降级走现有 loader 链，legacy Run 兼容）。

        防投毒校验（LLM 凭据红线）：快照 base_url 与当前受控配置
        （llm_config loader）一致才可用——快照本身不含 key，key 由受控通道
        现取，若快照 base_url 指向他人端点，真实 key 就会发往该端点（内网
        抢注绑定即可窃取）。受控配置为 None（未配置 LLM）时同样弃用快照：
        无从证明快照端点可信，model 锁定随之放弃——安全优先。
        """
        try:
            from app.platform.agent.binding_client import get_binding_client

            record = get_binding_client().get_binding(trace_id)
            if record is None or record.llm_config is None:
                return None
            snapshot = record.llm_config

            from app.platform.llm_config.loader import get_active_llm_config

            controlled = get_active_llm_config()
            if controlled is None or controlled.base_url != snapshot.base_url:
                logger.warning(
                    "resume 绑定快照 base_url 与受控配置不一致，弃用快照（退回 loader 链）"
                    ": trace=%s snapshot_base=%r controlled_base=%r",
                    trace_id, snapshot.base_url,
                    None if controlled is None else controlled.base_url,
                )
                return None
            return snapshot
        except Exception:  # noqa: BLE001
            return None

    def _gate_resume(self, trace_id: str):
        """resume 兼容门禁（FR-005）：decision=incompatible 时恢复被拒。

        Returns: ResumeCheck | None。None（无绑定记录，如迁移前 legacy Run）或
        decision=compatible → 照旧恢复。
        """
        from app.platform.agent.binding_client import get_binding_client

        return get_binding_client().resume_check(trace_id)

    def _reject_resume(self, thread: ThreadSummary, trace_id: str, reason: str) -> None:
        """恢复被拒后把 run 收敛为终态（cancel reason=incompatible_resume）。

        收敛逻辑封装在 recorder.cancel_awaiting_run（内存活跃态直接 cancel；
        重启后内存丢失先按 index 重建再 cancel；非 awaiting 不动账面）。
        收敛失败只记日志：门禁本身已拒绝续跑，终态收敛是尽力而为的账面清理。
        """
        try:
            self.trace_recorder.cancel_awaiting_run(
                thread, trace_id, reason="incompatible_resume",
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "incompatible_resume 终态收敛失败: trace=%s reason=%s",
                trace_id, reason, exc_info=True,
            )

    # ── 生成主链路 ─────────────────────────────────────────────────

    def generate(
        self,
        payload: ScreenplayGenerateRequest,
        thread: ThreadSummary,
        *,
        owner_id: str | None = None,
    ) -> ScreenplayGenerateResponse:
        if self.settings.writer_agent_mode.lower() == "mock":
            return self._mock_response(payload, thread)

        trace = self.trace_recorder.create_run(thread, "screenplay.generate")
        try:
            from app.domains.writing.pipeline import route as pipeline_route

            pipe = pipeline_route(
                Path(thread.workspace_path), thread.thread_id,
                user_text=payload.primary_text() or "",
                fresh_session=bool(getattr(payload, "fresh_session", False)),
            )
            prompt = self._build_user_prompt(payload, thread, directive=pipe.directive)
            model = self._resolve_model(owner_id)
            agent = self._agent_for_workspace(
                Path(thread.workspace_path), trace.trace_id, thread.workspace_id,
                model=model, owner_id=owner_id, agent_name=pipe.agent_name,
            )
            # T13：记录主控 prompt 版本到 trace（供后期 badcase 回放对照）。
            if hasattr(self, "_current_prompt_version"):
                self.trace_recorder.set_prompt_version(
                    trace.trace_id, "meta_system", self._current_prompt_version
                )
            result = agent.invoke(
                {"messages": [{"role": "user", "content": prompt}]},
                config={
                    "configurable": {"thread_id": pipe.thread_id},
                    "callbacks": [TraceCallbackHandler(self.trace_recorder, trace.trace_id)],
                    # 保险丝（REQ-20261002-125538 DEC-002）：正常停止由 QuotaConvergence
                    # 软着陆主导（预算 60 次模型调用 × 实测 ≈7.1 超步/次 ≈ 426），
                    # 此处远离预算只做最后保险丝，防超步系数漂移再炸硬顶。
                    "recursion_limit": 1000,
                },
            )
            content = self._extract_text(result)
            response = self._response_from_workspace_artifacts(payload, content, thread)
            # storybuilding 产物稳定后派生流程图（幂等：非 storybuilding 调用时 storyline.md 未变，图不变）
            generate_storyline_graph(Path(thread.workspace_path))
            self.trace_recorder.complete_run(thread, trace.trace_id)
            return response
        except BaseException as exc:
            self.trace_recorder.fail_run(thread, trace.trace_id, exc)
            raise

    def _sweep_stale_active_traces(self, workspace_path: Path, thread: ThreadSummary) -> None:
        """DEC-010 存量自愈（惰性）：清 state.json 里指向终态/消失 trace 的僵尸条目。

        旧断连分支漏清理的 active_traces 会让 resume 路由误配。每次开跑前只
        保留 trace 仍处 running / awaiting_input 的条目。best-effort，不阻断主流程。
        """
        try:
            from contracts.writing_artifacts import load_writing_state, save_writing_state

            state = load_writing_state(workspace_path)
            if not state.active_traces:
                return
            stale: list[str] = []
            for agent_key, trace_id in state.active_traces.items():
                if not trace_id:
                    continue
                run = self.trace_recorder.find_run_by_trace_id(trace_id)
                if run is None or run.status not in ("running", "awaiting_input"):
                    stale.append(agent_key)
            if not stale:
                return
            for agent_key in stale:
                state.active_traces.pop(agent_key, None)
            save_writing_state(workspace_path, state)
            logger.info("active_traces 僵尸清理 workspace=%s removed=%s", workspace_path, stale)
        except Exception:  # noqa: BLE001 — 加速层自愈，不得阻断创作主流程
            logger.warning("active_traces 僵尸清理失败（best-effort）", exc_info=True)

    async def _remove_messages(self, agent, config: dict, removes: list, snap) -> None:
        """从线程状态移除消息（RemoveMessage）。有 pending task 时需显式 as_node
        （挂在哪个节点名下不影响语义：后续新输入会开新超步）。"""
        as_node = None
        tasks = getattr(snap, "tasks", None) or ()
        if tasks:
            as_node = tasks[0].name
        await agent.aupdate_state(config, {"messages": removes}, as_node=as_node)

    async def _normalize_thread_tail(self, agent, config: dict) -> int:
        """清理粘性线程尾部的悬空轮次（FR-003 风险#1：重试原话重发不得重复）。

        上一轮取消/失败/进程崩溃时 checkpoint 残留：无回复的 user 消息、或未闭合
        的 assistant(tool_calls)+tool 链——直接追加新输入会重复提问，甚至触发
        模型 API 的 tool_calls 配对校验错误。规则：最后一个「无 tool_calls 的
        assistant」之后的尾部若以 user 消息开头，整段移除；有未决 interrupt
        （state.tasks 非空）不动（等 Command(resume)）。best-effort。
        """
        try:
            snap = await agent.aget_state(config)
            if not snap:
                return 0
            # 只有真挂着 HITL interrupt（等 Command(resume)）的线程不动；
            # aupdate_state 后的普通 pending task 不算（新输入会开新超步）。
            if any(getattr(t, "interrupts", None) for t in (snap.tasks or ())):
                return 0
            msgs = list((snap.values or {}).get("messages") or [])
            last_settled = -1
            for i in range(len(msgs) - 1, -1, -1):
                m = msgs[i]
                if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
                    last_settled = i
                    break
            tail = msgs[last_settled + 1:]
            if not tail or not isinstance(tail[0], HumanMessage):
                return 0
            removes = [RemoveMessage(id=m.id) for m in tail if getattr(m, "id", None)]
            if not removes:
                return 0
            await self._remove_messages(agent, config, removes, snap)
            logger.info(
                "粘性线程断尾清理 thread=%s removed=%d",
                config.get("configurable", {}).get("thread_id"), len(removes),
            )
            return len(removes)
        except Exception:  # noqa: BLE001 — 下轮开跑会再试，不阻断本轮
            logger.warning("断尾清理失败（best-effort）", exc_info=True)
            return 0

    async def _enforce_history_budget(self, agent, config: dict) -> int:
        """FR-007：会话历史估算超预算 → 从最老完整轮次裁剪 LLM 上下文。

        只裁 checkpoint 消息（模型输入侧）；会话消息日志与界面不动。估算用
        字符数（中文保守）。至少保留最近 2 个完整轮次。返回裁掉的轮数。
        """
        budget = int(getattr(self.settings, "conversation_history_char_budget", 96_000) or 0)
        if budget <= 0:
            return 0
        try:
            snap = await agent.aget_state(config)
            msgs = list((snap.values or {}).get("messages") or []) if snap else []
            if not msgs:
                return 0

            def _chars(m: Any) -> int:
                c = getattr(m, "content", "")
                if isinstance(c, str):
                    return len(c)
                if isinstance(c, list):
                    return sum(
                        len(str(p.get("text", ""))) if isinstance(p, dict) else len(str(p))
                        for p in c
                    )
                return 0

            turn_starts = [i for i, m in enumerate(msgs) if isinstance(m, HumanMessage)]
            if len(turn_starts) <= 2:
                return 0
            total = sum(_chars(m) for m in msgs)
            if total <= budget:
                return 0
            removed_turns = 0
            remove_ids: list[RemoveMessage] = []
            while total > budget and len(turn_starts) > 2:
                start = turn_starts.pop(0)
                end = turn_starts[0] if turn_starts else len(msgs)
                for m in msgs[start:end]:
                    total -= _chars(m)
                    if getattr(m, "id", None):
                        remove_ids.append(RemoveMessage(id=m.id))
                removed_turns += 1
            if not remove_ids:
                return 0
            await self._remove_messages(agent, config, remove_ids, snap)
            logger.info(
                "会话历史超预算裁剪 thread=%s turns=%d chars_left~%d",
                config.get("configurable", {}).get("thread_id"), removed_turns, total,
            )
            return removed_turns
        except Exception:  # noqa: BLE001 — 裁剪失败不阻断生成（宁可下次再裁）
            logger.warning("历史预算裁剪失败（best-effort）", exc_info=True)
            return 0

    async def generate_stream(
        self,
        payload: ScreenplayGenerateRequest,
        thread: ThreadSummary,
        *,
        owner_id: str | None = None,
        run_purpose: str = "user_generation",
        traceparent: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream agent execution events as SSE to the frontend.

        SSE 编排骨架（心跳 + astream_events 循环 + interrupt 检测）由
        ``platform.streaming.run_agent_stream`` 提供，事件分发逻辑通过
        闭包 sink 注入（领域专属：model_output/tool_call/task 派发/章节计数）。

        run_purpose（Phase 3 T3.1）：A/B 回放传 "optimization"，写进 trace 的
        run_start.input，供监测层防自指断路（optimization trace 不进优化池）。
        """
        if self.settings.writer_agent_mode.lower() == "mock":
            response = self._mock_response(payload, thread)
            yield _sse("final", response.model_dump())
            return

        # ── FR-005 消息台账：用户意图先落档（ledger），失败即拒绝本轮 ────────
        conversation_id = thread.thread_id
        workspace_path = Path(thread.workspace_path)
        conv_log = ConversationLog(workspace_path)
        user_text = payload.primary_text() or ""
        try:
            user_msg_id = conv_log.admit_user(
                conversation_id,
                user_text,
                message_id=getattr(payload, "message_id", None),
            )
        except ConversationLogError:
            logger.exception("会话消息落档失败，拒绝本轮 conv=%s", conversation_id)
            yield _sse("status", {
                "status": "log_failed",
                "error": "会话日志写入失败，本轮已拒绝；请重试。",
            })
            return
        if user_msg_id is None:
            # FR-006：该消息已被停止清队作废——拒绝借重发复活，按已取消收口。
            yield _sse("status", {
                "status": "cancelled",
                "message_id": getattr(payload, "message_id", None),
            })
            return

        # ── FR-006 会话串行门：同会话同刻只跑一轮，后到排队（FIFO）────────
        gate = get_conversation_gate(conversation_id)
        if not await gate.try_acquire():
            yield _sse("status", {"status": "queued", "message_id": user_msg_id})
            entered_epoch = gate.epoch
            waiter = asyncio.current_task()
            gate.register_waiter(waiter)
            # FIFO 保位：acquire 任务整个排队期只创建一次，心跳用 asyncio.wait
            # 计时、绝不取消它——wait_for 超时会取消并重排队尾，15s 心跳后
            # 队序就被打乱了（review R-003）。
            acquire_task = asyncio.ensure_future(gate.wait_acquire())
            exited_cleanly = False
            try:
                while True:
                    done, _ = await asyncio.wait({acquire_task}, timeout=_QUEUE_HEARTBEAT_S)
                    if acquire_task.done():
                        acquire_task.result()  # 取出结果（Lock.acquire 不会失败）
                        exited_cleanly = True
                        break
                    yield ": ping\n\n"  # 排队期心跳（客户端 45s 断流阈值）
            finally:
                gate.unregister_waiter(waiter)
                if not exited_cleanly:
                    # 排队期退出（断连/停止清队/取消落在 ping yield）：放弃排队
                    # 并落状态——放 finally 保证所有退出路径都有账。
                    # 停止清队（epoch 前进）→ cancelled；连接断开 → paused
                    # （DEC-009：保留待发，客户端回访后按序补跑）。
                    # 锁归还（R2-P1）：授予可能先于取消观察落地——等待者挂在
                    # ping yield 上没看见 acquire_task 已 done，cancel 对已完成
                    # 任务是 no-op，锁会悬死。done-callback 兜底归还。
                    def _return_lock_if_granted(task: asyncio.Task) -> None:
                        if not task.cancelled() and task.exception() is None:
                            gate.release()

                    acquire_task.add_done_callback(_return_lock_if_granted)
                    acquire_task.cancel()
                    conv_log.mark_user(
                        conversation_id, user_msg_id,
                        "cancelled" if gate.epoch != entered_epoch else "paused",
                    )
            if gate.epoch != entered_epoch:
                gate.release()
                conv_log.mark_user(conversation_id, user_msg_id, "cancelled")
                yield _sse("status", {"status": "cancelled", "message_id": user_msg_id})
                return
        conv_log.mark_user(conversation_id, user_msg_id, "running")

        # 半截正文留档（FR-004）：从本连接外发的帧里嗅探正文增量/正文行，
        # 断连时随 interrupted 落档。行模式与旧增量双通道，互不重叠消费。
        plain_parts: list[str] = []
        row_order: list[str] = []
        row_buf: dict[str, str] = {}
        row_kinds: dict[str, str] = {}

        def _partial_content() -> str:
            if plain_parts:
                return "".join(plain_parts)
            return "".join(row_buf.get(rid, "") for rid in row_order)

        def _sniff_frame(frame: str) -> None:
            if not frame.startswith("event: "):
                return
            try:
                head, data_line = frame.split("\n", 2)[:2]
                etype = head[len("event: "):].strip()
                if not data_line.startswith("data: "):
                    return
                data = json.loads(data_line[len("data: "):])
            except (ValueError, json.JSONDecodeError):
                return
            if etype == "model_stream":
                plain_parts.append(str(data.get("content", "")))
            elif etype == "row" and isinstance(data, dict):
                op = data.get("op")
                rid = str(data.get("id", ""))
                if op == "open":
                    row_kinds[rid] = str(data.get("kind", ""))
                    if row_kinds[rid] == "text" and rid not in row_order:
                        row_order.append(rid)
                    row_buf.setdefault(rid, "")
                elif op == "delta" and row_kinds.get(rid) == "text":
                    row_buf[rid] = row_buf.get(rid, "") + str(data.get("text", ""))
                elif op == "end" and row_kinds.get(rid) == "text" and data.get("text"):
                    row_buf[rid] = str(data.get("text", ""))  # end 携全量快照

        assistant_id: str | None = None
        assistant_closed = False

        def _close_assistant(status: str, content: str = "") -> None:
            """助手记录收尾（幂等）：两阶段写的补字段动作。"""
            nonlocal assistant_closed
            if assistant_id is None or assistant_closed:
                return
            conv_log.close_assistant(
                conversation_id, assistant_id, status=status, content=content
            )
            assistant_closed = True

        trace: Any = None
        agent_name: str | None = None
        try:

            # 多用户：分库 checkpointer 先解析；model 构建推迟到 Run 绑定之后
            # （FR-004 Run 级模型锁定：绑定快照有值时 model/base_url 锁定为快照值）。
            checkpointer = await self._resolve_checkpointer(owner_id)

            # DEC-010 存量自愈：清 state.json 里指向终态/消失 trace 的僵尸
            # active_traces（旧断连分支漏清理的遗留），resume 路由不再误配。
            self._sweep_stale_active_traces(workspace_path, thread)

            # HITL resume：带 resume + trace_id 时复用活跃 trace（不发 run_start），
            # 否则 create_run 新开。内存丢失（服务重启）时 resume_run 会降级 create_run（D2=A）。
            # resume trace 失配（resume_degraded）按普通消息续跑粘性线程（FR-002 边界）。
            resume_value = getattr(payload, "resume", None)
            trace_id_in = getattr(payload, "trace_id", None)
            llm_override = None
            binding = None

            # 五段式管线路由（REQ-20261018-115508 FR-002 + REQ-20261019-222403 FR-001 粘性）：
            # 决定当前大段任务书与用哪个会话 thread；resume 沿未决拍板的原会话续跑；
            # fresh_session=丢弃重开（作废旧 trace，递增派生号开新线程）。
            from app.domains.writing import pipeline as writing_pipeline
            from app.domains.writing.pipeline import (
                pipeline_status,
                pop_awaiting_trace,
                route as pipeline_route,
            )

            pipe_decision = pipeline_route(
                workspace_path, thread.thread_id,
                user_text=user_text,
                resume_trace=trace_id_in if resume_value is not None else None,
                fresh_session=bool(getattr(payload, "fresh_session", False)),
            )
            agent_name = pipe_decision.agent_name
            lg_thread_id = pipe_decision.thread_id
            if getattr(payload, "fresh_session", False) and pipe_decision.new_session:
                old_trace = pop_awaiting_trace(workspace_path, agent_name)
                if old_trace:
                    try:
                        self.trace_recorder.cancel_awaiting_run(
                            thread, old_trace, reason="session_abandoned",
                        )
                    except Exception:  # noqa: BLE001 — 账面清理尽力而为
                        logger.warning("丢弃重开：作废旧 trace 失败 trace=%s", old_trace)

            if resume_value is not None and trace_id_in and not pipe_decision.resume_degraded:
                # FR-005 resume 兼容门禁 + FR-004 绑定快照：两次网络往返并行发出
                # （同步 HTTP，各自 to_thread 放线程池，不阻塞事件循环）。
                # incompatible 分支行为不变：仍拒绝恢复、不进 resume 流程。
                check, bound_snapshot = await asyncio.gather(
                    asyncio.to_thread(self._gate_resume, trace_id_in),
                    asyncio.to_thread(self._resume_binding_snapshot, trace_id_in),
                )
                if check is not None and check.decision == "incompatible":
                    self._reject_resume(thread, trace_id_in, check.reason)
                    yield _sse("status", {
                        "status": "resume_rejected",
                        "trace_id": trace_id_in,
                        "reason": f"恢复被拒:版本不兼容（{check.reason}）",
                    })
                    return
                trace, is_new = self.trace_recorder.resume_run(thread, trace_id_in)
                if not is_new:
                    # FR-004：续跑 Run 的模型配置用绑定快照（无绑定/查询失败降级现有链）
                    llm_override = bound_snapshot
            else:
                trace = self.trace_recorder.create_run(
                    thread,
                    "screenplay.generate.stream",
                    run_purpose=run_purpose,
                    traceparent=traceparent,
                )
                is_new = True
            if is_new:
                yield _sse("status", {"status": "started", "trace_id": trace.trace_id})
                # 第二期证据采集（2026-07）：新 trace 启动时冻结任务契约快照。
                # resume 分支不 emit（契约在初始 run 已冻结）。
                _emit_contract_snapshot(self.trace_recorder, trace.trace_id, payload, thread, run_purpose)
                # FR-004：新 Run 签发绑定（fail-static：Platform 不可达时 degraded 本地
                # 记录 + spool 补账，绝不阻塞生成）。快照同时锁定本 Run 的模型配置。
                # 同步 HTTP（bind 一次往返）放线程池，不阻塞事件循环。
                llm_override, binding = await asyncio.to_thread(
                    self._issue_run_binding, trace.trace_id, run_purpose
                )
            # FR-005 助手记录开壳：本轮 run 的服务端留档起点（两阶段写之前半）。
            assistant_id = conv_log.open_assistant(conversation_id, trace.trace_id)
            model = (
                build_writer_model(self.settings, llm_override=llm_override)
                if llm_override is not None
                else self._resolve_model(owner_id)
            )
            trace_queue = self.trace_recorder.get_active_queue(trace.trace_id)
            if trace_queue is None:
                raise RuntimeError(f"Trace queue was not created: {trace.trace_id}")
            # 登记 SSE 生成器 task，让 POST /stop 能跨请求 task.cancel()（弥补浏览器
            # 刷新/关闭/cloudflared 掐断后 abortController 丢失导致后台 trace 停不掉的缺口）。
            # 清理由下方 try/finally 保证（正常/异常/CancelledError 三路都 unregister）。
            self.trace_recorder.register_run_task(trace.trace_id, asyncio.current_task())
            for trace_update in self._trace_updates(trace_queue):
                yield trace_update

            agent = self._agent_for_workspace(
                Path(thread.workspace_path), trace.trace_id, thread.workspace_id,
                model=model, checkpointer=checkpointer, owner_id=owner_id,
                strict_evidence=run_purpose != "user_generation",
                llm_override=llm_override, binding=binding,
                agent_name=agent_name,
            )

            # 管线状态落盘（会话与 active trace 登记）+ 桌面端阶段条事件（FR-010）
            # R3 修复：状态写是加速层（目录即进度），失败 best-effort 不裸断 SSE。
            try:
                writing_pipeline.commit_route(workspace_path, pipe_decision, trace.trace_id)
            except OSError:
                logger.warning(
                    "管线状态落盘失败（best-effort，目录即进度可重建）: %s", workspace_path,
                    exc_info=True,
                )
            try:
                yield _sse("pipeline", {
                    **pipeline_status(workspace_path),
                    "agent": agent_name,
                })
            except Exception:  # noqa: BLE001 — 观测事件不得阻塞主流程
                pass

            config = {
                "configurable": {"thread_id": lg_thread_id},
                "callbacks": [TraceCallbackHandler(self.trace_recorder, trace.trace_id)],
                # 保险丝（REQ-20261002-125538 DEC-02）：正常停止由 QuotaConvergence
                # 软着陆主导（预算 60 次模型调用 × 实测 ≈7.1 超步/次 ≈ 426），
                # 此处远离预算只做最后保险丝，防超步系数漂移再炸硬顶。
                # 触发本保险丝时由 except GraphRecursionError 分支做部分成功收尾。
                "recursion_limit": 1000,
            }

            # ── 粘性线程前置整理（REQ-20261019-222403）────────────────────
            # FR-003：非 resume 轮先清掉上一轮取消/崩溃残留的悬空尾段——重试
            # 原话重发不得重复，未闭合 tool_calls 链不得带进下一次模型调用。
            if resume_value is None or pipe_decision.resume_degraded:
                await self._normalize_thread_tail(agent, config)
            # FR-007：会话历史估算超预算 → 从最老完整轮次裁剪 LLM 输入
            # （消息日志与界面不动，仅裁模型上下文），触发即广播一次提示。
            dropped_turns = await self._enforce_history_budget(agent, config)
            if dropped_turns:
                yield _sse("status", {
                    "status": "history_truncated",
                    "dropped_turns": dropped_turns,
                })

            # resume 分支已在上方判定，据此构造 agent 输入（degraded 按普通消息）
            if resume_value is not None and not pipe_decision.resume_degraded:
                agent_input: object = Command(resume=resume_value)
            else:
                prompt = self._build_user_prompt(payload, thread, directive=pipe_decision.directive)
                agent_input = {"messages": [{"role": "user", "content": prompt}]}

            # trace_pump 作为额外并发任务（与 agent 事件/心跳公平竞争 asyncio.wait）
            def _on_trace_done(finished: asyncio.Task) -> tuple[asyncio.Task | None, list[str]]:
                update = finished.result()
                return asyncio.create_task(_next_trace_update(trace_queue)), [update] if update else []

            trace_extra = ExtraTask(
                task=asyncio.create_task(_next_trace_update(trace_queue)),
                on_done=_on_trace_done,
            )

            # 领域事件分发：章节号/字数/task 派发等 writing 专属逻辑封装在 WritingEventSink（M4 抽离）
            # FR-002：传入 trace_recorder + trace_id，供 sink 构造章节抽取入库埋点回调（写 trace run_meta）。
            sink = WritingEventSink(thread, self.trace_recorder, trace.trace_id)

            sse_iter, result = await run_agent_stream(
                agent, agent_input, config,
                sink=sink,
                extra_tasks=[trace_extra],
            )

            try:
                async for frame in sse_iter:
                    _sniff_frame(frame)
                    yield frame

                # ── 流结束：trace drain + interrupt 检测 + final ──────────────────────
                for trace_update in self._trace_updates(trace_queue):
                    yield trace_update

                # T6.2：正常完成时结算预扣（多退少补 + 落流水 D23）
                self._settle_credits_if_any(thread.thread_id, force_stopped=False)

                if result.pending_interrupt is not None:
                    iv = result.pending_interrupt
                    # HITL：interrupt 命中 → 标记 trace awaiting_input 并落盘（D1）。
                    # domain 层决定调用时机；recorder 写 run_awaiting + 更新 index +
                    # notify evolution（不清内存，等 Command(resume) 续接）。
                    self.trace_recorder.await_input_run(thread, trace.trace_id)
                    try:
                        writing_pipeline.on_interrupt(workspace_path, agent_name, trace.trace_id)
                    except OSError:
                        logger.warning("on_interrupt 状态写失败（best-effort）", exc_info=True)
                    interrupt_payload: dict[str, Any] = {
                        "thread_id": thread.thread_id,
                        "source": "interview",
                    }
                    if isinstance(iv, dict):
                        # DD4（hitl.py 协议）：优先转发 payload 自带 kind/source——
                        # 如回执轮 confirm_with_user 的 kind="choice" +
                        # source="storybuilding-receipt"；缺省回退访谈旧口径
                        # （source=interview / 前端 kind 默认 choice），向后兼容。
                        if isinstance(iv.get("kind"), str):
                            interrupt_payload["kind"] = iv["kind"]
                        if isinstance(iv.get("source"), str):
                            interrupt_payload["source"] = iv["source"]
                        interrupt_payload["question"] = iv.get("question", "")
                        interrupt_payload["options"] = iv.get("options")
                        interrupt_payload["multi_select"] = iv.get("multi_select", False)
                    # FR-005：提问腿收 awaiting（答案腿 resume 时开新助手记录）。
                    _close_assistant("awaiting", str(interrupt_payload.get("question", "")))
                    yield _sse("interrupt", interrupt_payload)
                    return

                if result.full_result is None:
                    raise ValueError("Agent stream ended without a LangGraph final output.")
                content = self._extract_text(result.full_result)
                response = self._response_from_workspace_artifacts(payload, content, thread)
                self.trace_recorder.complete_run(thread, trace.trace_id)
                # FR-005：助手记录收尾（completed + 全文）。
                _close_assistant("completed", content)
                try:
                    writing_pipeline.on_run_complete(workspace_path, agent_name)
                except OSError:
                    logger.warning("on_run_complete 状态写失败（best-effort）", exc_info=True)
                # 相位推进即时广播（如主题完成→总纲）：on_run_complete 重扫后的相位
                # 直接送桌面端挂完成卡片，不再依赖停前补拉/轮询的时序兜底。
                try:
                    yield _sse("pipeline", {
                        **pipeline_status(workspace_path),
                        "agent": agent_name,
                    })
                except Exception:  # noqa: BLE001 — 观测事件不得阻塞主流程
                    pass
                for trace_update in self._trace_updates(trace_queue):
                    yield trace_update
                snapshot = self.trace_recorder.read_run_snapshot(thread, trace.trace_id)
                if snapshot is None:
                    raise RuntimeError(f"Trace snapshot was not found: {trace.trace_id}")
                yield _sse("trace_snapshot", snapshot.model_dump(mode="json"))
                yield _sse("final", response.model_dump())
            except GraphRecursionError:
                # FR-004（REQ-20261002-125538）：撞步数保险丝 → 部分成功收尾，不裸抛。
                # 保险丝（1000）远离软着陆预算（60 次调用 × ≈7.1 超步 ≈ 426），
                # 走到这里说明软着陆同时失效的极端场景——已写内容对用户仍有价值。
                self.trace_recorder.step_limit_run(thread, trace.trace_id)
                step_response = self._step_limit_response(payload, thread)
                _close_assistant("completed", getattr(step_response, "content", "") or "")
                # 已消耗的 token 不退（与 user_stop 同口径结算）
                self._settle_credits_if_any(thread.thread_id, force_stopped=False)
                for trace_update in self._trace_updates(trace_queue):
                    yield trace_update
                yield _sse("final", step_response.model_dump())
                return
            except asyncio.CancelledError:
                # D4/D5/D6 + FR-002（REQ-20261019-222403）：CancelledError 分流。
                # trace 未建（排队/早期取消）时无账可收，直接上抛。
                if trace is None:
                    raise
                # - user_stop：用户点了停止按钮（_user_stop_requested 标记命中）→ cancelled
                # - awaiting_input：interrupt 期间连接断开 → 保持 awaiting_input 不收尾（可 resume）
                # - 否则：running 期间连接断开 → interrupted（会话可续：本轮作废、
                #   重试按原话重发；区别于用户作废的 cancelled 终态）
                if self.trace_recorder.is_user_stop_requested(trace.trace_id):
                    _close_assistant("stopped", _partial_content())
                    self.trace_recorder.cancel_run(thread, trace.trace_id, reason="user_stop")
                    try:
                        writing_pipeline.on_run_abandoned(workspace_path, agent_name)
                    except OSError:
                        logger.warning("on_run_abandoned 状态写失败（best-effort）", exc_info=True)
                    # 用户停止也要结算预扣（已消耗的 token 不退）
                    self._settle_credits_if_any(thread.thread_id, force_stopped=False)
                elif self.trace_recorder.is_awaiting_input(trace.trace_id):
                    _close_assistant("awaiting", _partial_content())
                    pass  # interrupt 期间断连：保持 awaiting_input，等用户 resume
                else:
                    _close_assistant("interrupted", _partial_content())
                    self.trace_recorder.interrupt_run(thread, trace.trace_id)
                    self._settle_credits_if_any(thread.thread_id, force_stopped=False)
                    # FR-002 修复：断连分支此前漏调 on_run_abandoned，state.json 残留
                    # 僵尸 active_traces（resume 误配根因之一）——补齐清理。
                    try:
                        writing_pipeline.on_run_abandoned(workspace_path, agent_name)
                    except OSError:
                        logger.warning("on_run_abandoned 状态写失败（best-effort）", exc_info=True)
                raise
            except Exception as exc:
                # FR-007：credit_exhausted 强停分流已随免计费移除（原 T6.1/T6.3）
                from app.platform.agent.middleware.artifact_capture import EvidenceCaptureError

                if trace is not None:
                    _close_assistant("failed", _partial_content())
                    if isinstance(exc, EvidenceCaptureError) and run_purpose != "user_generation":
                        self.trace_recorder.fail_evidence_capture_run(thread, trace.trace_id, exc)
                    else:
                        self.trace_recorder.fail_run(thread, trace.trace_id, exc)
                    for trace_update in self._trace_updates(trace_queue):
                        yield trace_update
                raise
            finally:
                # 兜底清理 task 注册（正常/异常/CancelledError 三路都走到）。
                # _cleanup_run_state 也会兜底，但这里更早执行避免泄漏窗口。
                if trace is not None:
                    self.trace_recorder.unregister_run_task(trace.trace_id)
                    # 终态兜底（第四路）：except 分支之外离开（生成器被 close/GC 等）时
                    # run 会停在 running，进化端永久显示"执行中"——按断连中断补写
                    # （FR-002：会话可续语义，非用户作废）。
                    self.trace_recorder.finalize_orphan_run(thread, trace.trace_id)
                # 未收尾的助手记录按 interrupted 落档（半截内容保真，FR-004）。
                _close_assistant("interrupted", _partial_content())
        finally:
            gate.release()

    def _trace_updates(self, trace_queue) -> list[str]:
        trace_events = _drain_trace_queue(trace_queue)
        if not trace_events:
            return []
        return [_sse("trace_event", trace_event) for trace_event in trace_events]

    def _settle_credits_if_any(self, thread_id: str, *, force_stopped: bool) -> None:
        """T6.2：结算当前 thread 的活跃预扣（如有）。

        正常完成 / 用户停止 / 客户端断连时调 force_stopped=False。
        CreditExhaustedError 强停时调 force_stopped=True。
        无活跃预扣时静默跳过（interview 阶段 / 非计费路径）。
        """
        try:
            from app.platform.credits.service import get_credits_service
            svc = get_credits_service()
            hold = svc.get_active_hold(thread_id)
            if hold:
                svc.settle_hold(hold["hold_id"], force_stopped=force_stopped)
        except RuntimeError:
            pass  # CreditsService 未初始化（A/B 测试 / 管理员路径），跳过
        except Exception:
            pass  # 结算失败不影响创作主流程（已落盘的内容不丢失）

    def _build_user_prompt(
        self,
        payload: ScreenplayGenerateRequest,
        thread: ThreadSummary,
        *,
        directive: str | None = None,
    ) -> str:
        """组装本轮 user prompt——只许「信封 + 运行时数据」，禁止行为指令。

        【注入禁令（2026-10-19 线上事故）】user prompt 里不得出现任何指导模型
        「怎么开场、怎么提问、产出什么、遵守什么纪律」的行为指令（含流程介绍、
        产物要求、问法模板、段任务书）。行为指令的唯一居所是发版提示词——
        常驻骨架 author_base.md + 段规程 stage_*.md，经 harness 包发版热切换。
        在代码里硬编码的行为指令作者改不到、发版盖不掉，会直接顶掉发版效果
        （事故：此处曾硬编码「新书首轮（硬性）第 0 步开场」，发版删掉第 0 步
        后线上仍每轮问书名/类型）。新增注入前先自问：这段话是不是行为指令？
        是 → 写进提示词发版，不写代码。

        本函数允许的内容：会话信封（目录/session/输出格式）、管线 directive
        （当前段+进度等磁盘事实，见 pipeline.route）、旧客户端表单内容透传、
        作者消息与结构化字段。
        """
        context_lines = [
            f"{key}: {value}"
            for key, value in payload.loose_context().items()
            if value not in ("", [], {})
        ]
        free_text = payload.primary_text()
        context = "\n".join(context_lines) or "作者没有提供结构化字段。"
        request_text = free_text or "请按当前段规程继续推进。"

        directive_section = f"{directive}\n\n" if directive else ""

        # 旧客户端兼容垫片（A1 修复）：已发布桌面端的表单仍会发 demand_md——
        # 表单入口已砍（DEC-003），但其内容作为摸底素材并入本轮消息，走访谈
        # 流程消化（题材段照常逐项确认），不静默丢弃。这是作者自己提交的数据
        # 透传，不是行为指令注入。
        demand_md = getattr(payload, "demand_md", None)
        demand_section = (
            "## 旧版客户端表单内容（摸底素材）\n\n"
            "以下是旧版桌面端表单提交的内容，按摸底素材处理：先复述要点并向作者确认，"
            "再按当前段流程逐项走访谈，不直接当作已确认需求：\n\n"
            f"{demand_md.strip()[:4000]}\n\n"
            if demand_md and demand_md.strip()
            else ""
        )

        return (
            "请根据作者消息推进创作。\n"
            "当前工作目录：/\n"
            f"当前 session：{thread.thread_id}\n\n"
            f"{directive_section}"
            f"{demand_section}"
            "作者消息：\n"
            f"{request_text}\n\n"
            "可用上下文（字段可能不完整，也可能包含额外信息）：\n"
            f"{context}\n\n"
            "回复请使用自然语言纯文本，不要返回 JSON。"
        )

    def _mock_response(
        self,
        payload: ScreenplayGenerateRequest,
        thread: ThreadSummary,
    ) -> ScreenplayGenerateResponse:
        title = payload.fallback_title()
        premise = payload.primary_text() or "一个需要继续深化的故事创意"
        genre = payload.genre or "剧情"
        tone = payload.tone or "电影感"
        audience = payload.audience or "大众"
        beats = [
            f"开端：建立一个带有{tone}气质的{genre}世界，核心问题来自{premise}。",
            "推动：主角被迫投入一个会改变生活秩序的目标，并第一次付出真实代价。",
            "转折：看似有效的解决方案暴露出更深层的人物欲望和关系裂缝。",
            "低谷：主角失去最依赖的选择，只能面对自己一直回避的真相。",
            "高潮/结局：外部冲突被解决，主角也用行动证明故事真正的主题。",
        ]
        response = ScreenplayGenerateResponse(
            mode="mock",
            thread_id=thread.thread_id,
            workspace_id=thread.workspace_id,
            session_name=thread.session_name,
            workspace_path=thread.workspace_path,
            title=title,
            content="\n".join(beats),
            logline=f"在这个{genre}故事中，主角必须直面{premise!r}，否则将失去完成自我转变的机会。",
            synopsis=(
                f"《{title}》是一部面向{audience}的{tone}{genre}作品。故事从一个打破日常秩序的需求开始，"
                "逐步推进到更复杂的人物选择，并在结尾让主角用关键行动回应自己的核心困境。"
            ),
            beats=beats,
        )
        return response

    def _extract_text(self, result: object) -> str:
        if isinstance(result, dict):
            messages = result.get("messages", [])
            for message in reversed(messages):
                content = self._message_content(message)
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    text_chunks = []
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text_chunks.append(item.get("text", ""))
                    if text_chunks:
                        return "\n".join(chunk for chunk in text_chunks if chunk)
        return str(result)

    def _message_content(self, message: object) -> object:
        if isinstance(message, dict):
            return message.get("content")
        return getattr(message, "content", None)

    def _response_from_workspace_artifacts(
        self,
        payload: ScreenplayGenerateRequest,
        content: str,
        thread: ThreadSummary,
    ) -> ScreenplayGenerateResponse:
        """收尾锚点检查（FR-007/REQ-20260930-163019 沿革）。

        五段制（REQ-20261018-115508）：访谈轮可以只问答不落盘（题材段摸底、
        检验追问都是合法的无产物轮）——five 工作区不再强制产物锚点，回复即
        交付。legacy 工作区保留旧锚：旧五件套锚 storyline.md 非空；旧七步/
        三步制锚 outline/ 任一 .md。
        """
        from app.domains.writing.pipeline import classify_workspace

        title = payload.fallback_title()
        workspace_path = Path(thread.workspace_path)
        kind = classify_workspace(workspace_path)
        if kind == "five":
            return ScreenplayGenerateResponse(
                mode="live",
                thread_id=thread.thread_id,
                workspace_id=thread.workspace_id,
                session_name=thread.session_name,
                workspace_path=thread.workspace_path,
                title=title,
                content=content,
            )
        storyline_path = workspace_path / "storyline.md"
        outline_dir = workspace_path / "outline"
        if storyline_path.exists():
            if not storyline_path.read_text(encoding="utf-8").strip():
                raise ValueError(f"未产出故事线（storyline.md 为空）: {storyline_path}")
        else:
            # _pipeline.json 是状态文件不是产物，rglob("*.md") 天然排除
            if not (outline_dir.is_dir() and any(outline_dir.rglob("*.md"))):
                raise FileNotFoundError(f"未产出大纲（outline/ 目录无产物）: {outline_dir}")

        return ScreenplayGenerateResponse(
            mode="live",
            thread_id=thread.thread_id,
            workspace_id=thread.workspace_id,
            session_name=thread.session_name,
            workspace_path=thread.workspace_path,
            title=title,
            content=content,
        )

    def _step_limit_response(
        self,
        payload: ScreenplayGenerateRequest,
        thread: ThreadSummary,
    ) -> ScreenplayGenerateResponse:
        """步数保险丝触顶后的部分成功响应（FR-004，REQ-20261002-125538）。

        workspace 已有 storyline.md 产物 → 复用正常收尾构造（含锚定检查），
        content 前置降级文案；极端情况（保险丝烧断时尚无产物）→ 纯降级文案。
        """
        notice = "大纲已生成部分内容，因步数限制提前收尾，可继续对话补全。"
        try:
            return self._response_from_workspace_artifacts(payload, notice, thread)
        except (FileNotFoundError, ValueError):
            return ScreenplayGenerateResponse(
                mode="live",
                thread_id=thread.thread_id,
                workspace_id=thread.workspace_id,
                session_name=thread.session_name,
                workspace_path=thread.workspace_path,
                title=payload.fallback_title(),
                content=notice,
            )

def _sse(event_type: str, payload: object) -> str:
    """Format a single Server-Sent Event line."""
    data = json.dumps(payload, ensure_ascii=False, default=str)
    return f"event: {event_type}\ndata: {data}\n\n"


async def _next_trace_update(queue) -> str:
    event = await queue.get()
    return _sse("trace_event", event.model_dump())


def _drain_trace_queue(queue) -> list[dict]:
    events = []
    while not queue.empty():
        events.append(queue.get_nowait().model_dump())
    return events
