"""Noninteractive improvement in a private checkout, with optimization-only evidence."""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from openai import APIConnectionError, APIStatusError, DefaultAsyncHttpxClient

from app.core import db
from app.core.settings import settings
from app.experiments import clients, repo


class IncompleteImprovement(ValueError):
    """The chapter evolver ended without a valid decision or candidate."""


_MAX_COMPLETION_NUDGES = 3


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(["git", *arguments], cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=60)
    if result.returncode:
        raise RuntimeError("候选仓库操作失败：" + result.stderr[-500:])
    return result.stdout.strip()


def asset_candidate(run: dict, commit: str) -> dict:
    from app.versioning.asset_candidates import validate_candidate
    base = run["baseline"]["commit"]
    validate_candidate(commit, base)
    prepared = clients.prepare_artifact(commit)
    diff = _git(settings.harness_bare_repo_path, "diff", base, commit)
    return {"commit": commit, "digest": prepared["digest"], "diff": diff, "summary": "评测资产页创建的候选版本"}


def _save_assessment_version(run, record, root):
    from app.experiments import snapshots
    from app.versioning.asset_candidates import is_candidate_asset

    _git(root, "add", "--all", "--", ".")
    names = _git(root, "diff", "--cached", "--name-only", "-z").split("\0")
    if any(not is_candidate_asset(name) or (root / name).is_symlink() for name in names if name):
        raise ValueError("调整包含不属于正文 Harness 的资产")
    if any(names):
        _git(root, "-c", "user.name=Writer Evolution", "-c", "user.email=evolution@writer.local",
             "commit", "-m", f"Assessment {run['id']} round {record['round']}")
    commit = _git(root, "rev-parse", "HEAD")
    snapshots.save(root, commit)
    snapshots.retain(run["id"], commit)
    record.update(source_commit=commit, diff=_git(root, "diff", record["base_commit"], commit))
    repo.put_run(run)
    return commit


def _prepare_version(run, record, frozen):
    try:
        prepared = clients.prepare_artifact(frozen["commit"])
    except ValueError as exc:
        if run.get("assessment_version"):
            record["startup_check"] = {"status": "failed", "error": str(exc)}
            repo.put_run(run)
        raise
    if run.get("assessment_version"):
        record["startup_check"] = {"status": "passed"}
    return {**frozen, "digest": prepared["digest"]}


def _record_design(run, record, path):
    from app.evolve.docs import read_design_doc

    doc = read_design_doc(path)
    record["design"] = doc["body"]
    refs = {ref for change in doc["meta"].get("changes", []) for ref in change.get("evidence_ref", [])}
    record["evidence_samples"] = [s["id"] for s in run["samples"]
                                  if s["phase"] in {"baseline", "exploration"}
                                  and s["package_id"] in run["settings"]["optimization_ids"] and s["id"] in refs]


def improve_candidate(run: dict, round_record: dict, evidence: list[dict]) -> dict:
    """Freeze and prepare a candidate; this function never promotes production."""
    if round_record.get("frozen_candidate"):
        return _prepare_version(run, round_record, round_record["frozen_candidate"])
    root = settings.db_path.parent / "experiments" / run["id"] / f"round-{round_record['round']}"
    root.parent.mkdir(parents=True, exist_ok=True)
    private_version = run.get("flow_version") == 3 and run.get("task_type") == "chapter_text"
    if not (root / ".git").exists():
        # A separate clone has a separate index and HEAD; human sessions keep their checkout.
        if private_version:
            from app.experiments import snapshots
            snapshots.checkout_base(round_record["base_commit"], root)
        else:
            result = subprocess.run(["git", "clone", "--no-hardlinks", str(settings.harness_bare_repo_path), str(root)], capture_output=True, text=True, encoding="utf-8", timeout=60)
            if result.returncode:
                raise RuntimeError("无法创建候选工作副本")
            _git(root, "checkout", "-b", f"codex/{run['id']}-r{round_record['round']}", round_record["base_commit"])
    if any(path.is_symlink() for path in root.rglob("*") if ".git" not in path.parts):
        raise ValueError("候选包不接受符号链接")
    round_record["session_id"] = f"{run['id']}-r{round_record['round']}"
    round_record["work_root"] = str(root)
    repo.put_run(run)
    try:
        result = asyncio.run(_improve(run, round_record, root, evidence))
    except (APIConnectionError, APIStatusError) as exc:
        if isinstance(exc, APIConnectionError) or exc.status_code in {408, 409, 429} or exc.status_code >= 500:
            raise clients.ServiceUnavailable("进化模型暂时不可用：" + type(exc).__name__) from exc
        if private_version:
            _save_assessment_version(run, round_record, root)
        raise
    except Exception:
        if private_version:
            _save_assessment_version(run, round_record, root)
        raise
    if result.get("outcome") == "no_change":
        return result
    if private_version:
        round_record["summary"] = result.get("summary", "")
        commit = _save_assessment_version(run, round_record, root)
        if not result["validated"]:
            errors = _compile_candidate(root)
            reason = "候选源码校验未通过：" + ("、".join(errors) if errors else "最终改动尚未通过校验")
            round_record["startup_check"] = {"status": "failed", "error": reason}
            repo.put_run(run)
            raise ValueError(reason)
        if commit == round_record["base_commit"]:
            raise ValueError("候选未产生 Harness 改动，不能作为改版进入复测")
        frozen = {"commit": commit, "diff": round_record["diff"], **result}
        round_record["frozen_candidate"] = frozen
        repo.put_run(run)
        return _prepare_version(run, round_record, frozen)
    if not result["validated"]:
        raise ValueError("候选源码校验未通过")
    changed = _git(root, "status", "--porcelain")
    if changed:
        if run.get("flow_version") == 3:
            from app.versioning.asset_candidates import is_candidate_asset
            _git(root, "add", "--all", "--", ".")
            names = _git(root, "diff", "--cached", "--name-only", "-z").split("\0")
            if any(not is_candidate_asset(name) for name in names if name):
                raise ValueError("候选包含非本套 Harness 资产的改动")
        else:
            _git(root, "add", "--all", "--", ".", ":(exclude)registry.json")
        _git(root, "-c", "user.name=Writer Evolution", "-c", "user.email=evolution@writer.local", "commit", "-m", f"Experiment {run['id']} round {round_record['round']}")
    commit = _git(root, "rev-parse", "HEAD")
    if run.get("flow_version") in {2, 3} and commit == round_record["base_commit"]:
        raise ValueError("候选未产生 Harness 改动，不能作为改版进入复测")
    _git(root, "push", "origin", f"HEAD:refs/heads/codex/{run['id']}-r{round_record['round']}")
    diff = _git(root, "diff", round_record["base_commit"], commit, "--", ".", ":(exclude)registry.json")
    frozen = {"commit": commit, "diff": diff, **result}
    round_record["frozen_candidate"] = frozen
    repo.put_run(run)
    prepared = clients.prepare_artifact(commit)
    return {**frozen, "digest": prepared["digest"]}


def _compile_candidate(root: Path) -> list[str]:
    errors = []
    for path in root.rglob("*.py"):
        if ".git" in path.parts:
            continue
        try:
            compile(path.read_text(encoding="utf-8"), str(path.relative_to(root)), "exec")
        except (SyntaxError, UnicodeError) as exc:
            errors.append(f"{path.relative_to(root)}：{type(exc).__name__}：{exc}")
    return errors


def _system_prompt(assessment: bool) -> str:
    prompt = (
        "你是Writer Harness进化工程师。本实验已授权自动修改，无需逐点人工采纳。"
        "只允许在当前Harness包内修改提示词、技能、工具、中间件和装配代码。"
        "不得修改registry.json、.git、数据集、评测、权限、服务框架；不得寻找终验或其他作品数据。"
        "先探查并write_design_doc，再修改、validate_changes、write_change_log。运行时不允许调用发布。"
        "每次只处理有证据的问题，保留现有质量检查和取证行为。"
        "作出关键判断或改动时，在发起工具调用的同一条回复里，用普通消息简述具体依据和下一步。常规读取无需说明。"
    )
    if assessment:
        prompt += (
            "你负责正文 Agent 从题目到单章交稿的整体效果。先查第 0 轮原版："
            "用 read_assessment_round（round_number=0, section=samples）列出样本，"
            "再读取原版评分、终稿或写作 Trace。已复测的候选轮只用于补充证据。"
            "当前候选轮样本为空时，回到第 0 轮。"
            "读取 architecture.json 核对实际装配的提示词、技能、工具、中间件与子 Agent。"
            "可以调整这些资产及其装配关系。设计文档须注明问题、证据编号、拟改资产和预期效果。"
            "涉及改进题时，将样本列表返回的 evidence_ref 原样写入设计条目的 evidence_ref。"
            "只能查看本次档案；独立复测题只提供掉分项和幅度。"
            "\n\n正文进化先判断改法能否跨章节复用。单次错误可以作为线索。"
            "结合真实运行记录和现行规则或实际装配，查实会影响同类场景的共同成因后，才允许改动。"
            "把改法提炼为针对共因的规则或流程。作品名、章号、人物和情节等个案细节只留在证据和说明中。"
            "\n每项修正须在设计文档写明适用条件、具体改法、一个适用例和一个不适用例。"
            "假设例只用于说明适用边界，真实证据仍按上述规则引用。"
            "改法只适用于原样本或说不清边界时，先重新提炼。仍不成立就跳过。"
            "\n证据不足时先核查本次档案里的相关证据，仍无法查实就跳过该问题。"
            "只有查实通用缺口的修正才进入设计、修改、校验和改动记录流程。"
            "若查阅原版证据后仍没有符合条件的修正，保留原有资产，"
            "调用 finish_without_changes 提交理由和实际查阅的原版 evidence_ref。"
            "即使流程提示缺少产出，也禁止制造占位改动或虚构报告。"
        )
    return prompt


def _evolver_tools(backend, run: dict, ctx=None, *, on_baseline_evidence=None, finish_no_change=None):
    from langchain_core.tools import tool

    from app.evolve.agent.tools.flow import make_flow_tools
    from app.evolve.agent.tools.inspect import make_inspect_tools
    from app.evolve.agent.tools.writers import make_writer_tools

    @tool
    def validate_changes() -> str:
        """Compile candidate Python without executing imports; Platform does the isolated assembly check."""
        if ctx is None:
            raise RuntimeError("Validation requires an experiment checkout")
        errors = _compile_candidate(ctx.harness_root)
        ctx.validation_result = {"passed": not errors, "errors": errors, "env_diffs": []}
        ctx.code_mutations_since_validate = 0
        return json.dumps(ctx.validation_result, ensure_ascii=False)

    tools = make_inspect_tools() + make_writer_tools(backend)
    tools += [item for item in make_flow_tools() if item.name != "validate_changes"] + [validate_changes]
    if run.get("assessment_version"):
        from app.experiments.history import make_history_tools

        @tool
        def finish_without_changes(reason: str, evidence_refs: list[str]) -> str:
            """核查原版后确无合格改动时提交理由及实际读过的原版 evidence_ref。工作区须无改动。"""
            if finish_no_change is None:
                raise RuntimeError("Finishing requires an experiment checkout")
            return finish_no_change(reason, evidence_refs)

        tools.extend(make_history_tools(run, on_baseline_evidence=on_baseline_evidence))
        tools.append(finish_without_changes)
    return tools, validate_changes


def evolver_assets(*, assessment: bool) -> dict:
    from deepagents.backends import StateBackend

    # Tool definitions can be read without a checkout or an experiment's evidence.
    run = {"assessment_version": assessment, "settings": {"optimization_ids": []}}
    tools, _ = _evolver_tools(StateBackend(), run)
    return {"system_prompt": _system_prompt(assessment),
            "tools": [{"name": item.name, "description": item.description} for item in tools]}


async def _improve(run: dict, round_record: dict, root: Path, evidence: list[dict]) -> dict:
    from deepagents import create_deep_agent
    from deepagents.backends.filesystem import FilesystemBackend
    from langchain_openai import ChatOpenAI
    from langchain.agents.middleware import hook_config
    from langchain.agents.middleware.types import AgentMiddleware
    from langchain_core.messages import SystemMessage, ToolMessage
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from app.common.middleware.no_fs import NoFilesystemToolsMiddleware
    from app.common.model_factory import _ensure_gp_disabled
    from app.evolve import db as sessions
    from app.evolve.ctx import EvolveContext, set_tool_context
    from app.evolve.agent.agent import _run_agent_streamed
    from app.evolve.agent.middleware.flow_guard import FlowGuardMiddleware
    from app.experiments import runner
    from app.trace import TraceMiddleware, TraceCallbackHandler

    class EvolverModel(ChatOpenAI):
        def _get_request_payload(self, input_, *, stop=None, **kwargs):
            payload = super()._get_request_payload(input_, stop=stop, **kwargs)
            # Opt in only after LangChain has selected Responses, since adding
            # reasoning to a Chat Completions request would change its routing.
            model = self.model_name.lower()
            if "input" in payload and model.startswith(("gpt-5", "gpt-6", "o3", "o4")) and "-chat" not in model:
                reasoning = payload.get("reasoning") or {}
                if reasoning.get("effort") != "none":
                    payload["reasoning"] = {"summary": "auto", **reasoning}
            return payload

    class ExperimentSourceGuard(AgentMiddleware):
        def scoped_request(self, request):
            if run.get("flow_version") != 3:
                return request
            allowed = [item for item in request.tools if getattr(item, "name", None) in allowed_tools]
            return request.override(tools=allowed)

        def wrap_model_call(self, request, handler):
            if runner.is_stopped(run["id"]):
                raise asyncio.CancelledError()
            return handler(self.scoped_request(request))

        async def awrap_model_call(self, request, handler):
            if runner.is_stopped(run["id"]):
                raise asyncio.CancelledError()
            return await handler(self.scoped_request(request))

        def _check(self, request):
            if runner.is_stopped(run["id"]):
                raise asyncio.CancelledError()
            arguments = request.tool_call.get("args", {})
            path = arguments.get("file_path", arguments.get("path", ""))
            parts = Path(str(path).replace("\\", "/").lstrip("/")).parts
            tool_name = request.tool_call.get("name")
            if run.get("flow_version") == 3:
                from app.versioning.asset_candidates import is_candidate_asset
                if tool_name not in allowed_tools or (
                        tool_name in {"edit_source", "delete_file"} and not is_candidate_asset(str(path).lstrip("/"))):
                    return ToolMessage(content="当前工具或写入目标不属于本套候选资产。",
                                       status="error", tool_call_id=request.tool_call["id"])
            if any(part.startswith(".") for part in parts) or (parts and parts[0] in {"registry.json", "contracts", "dataset", "experiments"}):
                return ToolMessage(content="拒绝访问受保护的元数据或实验区域。", tool_call_id=request.tool_call["id"])
            return None

        def wrap_tool_call(self, request, handler):
            denied = self._check(request)
            return denied if denied is not None else handler(request)

        async def awrap_tool_call(self, request, handler):
            denied = self._check(request)
            return denied if denied is not None else await handler(request)

    assessment = bool(run.get("assessment_version"))
    if assessment and round_record.get("outcome") == "no_change":
        return {key: round_record[key] for key in ("outcome", "summary", "evidence_samples")}

    session_id = round_record["session_id"]
    old_session = sessions.get_session(session_id)
    if old_session and old_session.get("case_id") != "experiment:" + run["id"]:
        db.execute("UPDATE evolve_sessions SET case_id=? WHERE session_id=?", ("experiment:" + run["id"], session_id))
    if old_session and old_session["status"] == "done" and old_session.get("change_log_path"):
        if run.get("assessment_version") and old_session.get("design_doc_path"):
            _record_design(run, round_record, old_session["design_doc_path"])
        return {"validated": not _compile_candidate(root),
                "summary": Path(old_session["change_log_path"]).read_text(encoding="utf-8"), "usage": {"known": False}}
    if old_session is None:
        sessions.create_session(session_id, case_id="experiment:" + run["id"])
    ctx = EvolveContext(session_id)
    ctx.harness_root = root
    ctx.session_status = "running"
    ctx.recorder = runner.recorder()
    if old_session:
        ctx.design_doc_path = old_session.get("design_doc_path")
        ctx.change_log_path = old_session.get("change_log_path")
    set_tool_context(ctx)
    if ctx.recorder:
        handle = ctx.recorder.create_run(session_id=ctx.session_id, run_purpose="evolution_evolve",
                                        endpoint="experiments/improve", session_type="evolve", workload="evolution",
                                        external_refs={"experiment_id": run["id"], "round": str(round_record["round"]),
                                                       "phase": "improvement"})
        ctx.trace_id_self = handle.trace_id
    if ctx.trace_id_self:
        round_record.setdefault("trace_ids", []).append(ctx.trace_id_self)
        repo.put_run(run)

    baseline_reads = set(round_record.get("baseline_evidence_read", []))
    no_change = None

    def unchanged_checkout():
        return (not _git(root, "status", "--porcelain", "--untracked-files=all")
                and _git(root, "rev-parse", "HEAD") == round_record["base_commit"])

    def finish_no_change(reason, evidence_refs):
        nonlocal no_change
        reason = reason.strip()
        refs = list(dict.fromkeys(evidence_refs))
        if not reason or len(reason) > 1000:
            return "请提交 1 到 1000 字的无改动理由。"
        if not refs or not baseline_reads.issuperset(refs):
            return "请先读取第 0 轮原版评分、终稿、检查结果或写作记录，再引用实际读过的样本。"
        if not unchanged_checkout():
            return "工作区已有改动，请补齐候选校验和改动报告。"
        no_change = {"outcome": "no_change", "summary": reason, "evidence_samples": refs}
        return "已记录无改动结论，可以结束本轮。"

    def completion_problem():
        if not baseline_reads:
            return "请先读取第 0 轮原版样本的评分、终稿、检查结果或写作记录。"
        if no_change and unchanged_checkout():
            return None
        if not ctx.design_doc_path:
            return "请提交设计文档。若没有可复用的改法，请调用 finish_without_changes。"
        if ctx.code_mutations_since_validate or (ctx.validation_result is None and not ctx.change_log_path):
            return "请用 validate_changes 校验最终改动。"
        if not ctx.change_log_path:
            return "请用 write_change_log 提交改动报告。"
        return None

    class AssessmentCompletionGuard(FlowGuardMiddleware):
        def _check_completion(self):
            if runner.is_stopped(run["id"]):
                raise asyncio.CancelledError()
            problem = completion_problem()
            if not problem:
                return None
            count = round_record.get("completion_nudges", 0)
            if count >= _MAX_COMPLETION_NUDGES:
                raise IncompleteImprovement(problem)
            round_record["completion_nudges"] = count + 1
            round_record["baseline_evidence_read"] = sorted(baseline_reads)
            repo.put_run(run)
            ctx.emit_step("completion_check", "running", reminder=problem, attempt=count + 1)
            return {"messages": [SystemMessage(content=problem)], "jump_to": "model"}

        @hook_config(can_jump_to=["model"])
        def after_agent(self, state, runtime):
            return self._check_completion()

        @hook_config(can_jump_to=["model"])
        async def aafter_agent(self, state, runtime):
            return self._check_completion()

    backend = FilesystemBackend(root_dir=str(root), virtual_mode=True)
    tools, validate_changes = _evolver_tools(
        backend, run, ctx, on_baseline_evidence=baseline_reads.add, finish_no_change=finish_no_change)
    allowed_tools = {item.name for item in tools}
    _ensure_gp_disabled()
    credentials = repo.decrypt_credentials(run["private"]["evolver"])
    completion_guard = AssessmentCompletionGuard() if assessment else FlowGuardMiddleware()
    middleware = [NoFilesystemToolsMiddleware(), ExperimentSourceGuard(), completion_guard]
    if ctx.recorder and ctx.trace_id_self:
        middleware.append(TraceMiddleware(recorder=ctx.recorder, trace_id=ctx.trace_id_self, agent_name="experiment-evolver"))
    checkpoint_path = root.parent / f"round-{round_record['round']}.sqlite"
    prompt = _system_prompt(assessment)
    if assessment:
        from app.experiments.history import baseline_evidence_entries

        entries = json.dumps(baseline_evidence_entries(run), ensure_ascii=False)
        task = "先核查第 0 轮原版样本的实际证据，再决定是否改进。原版样本入口：" + entries
    else:
        task = "请依据给定的改进题证据修改候选并完成校验：\n" + json.dumps(evidence, ensure_ascii=False)
    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 200}
    if ctx.recorder and ctx.trace_id_self:
        from app.experiments.evolver_output import EvolverOutputCallback

        config["callbacks"] = [TraceCallbackHandler(ctx.recorder, ctx.trace_id_self), EvolverOutputCallback(ctx.trace_id_self)]
    try:
        # ChatOpenAI's default HTTP pool is cached across rounds, but each round
        # has its own asyncio.run loop. Close this round's pool before that loop.
        async with DefaultAsyncHttpxClient(timeout=300) as http_client, \
                AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
            model = EvolverModel(model=credentials["model"].split(":", 1)[-1], base_url=credentials["base_url"],
                                 api_key=credentials["api_key"], temperature=.2, max_tokens=8192,
                                 request_timeout=300, max_retries=1, http_async_client=http_client)
            agent = create_deep_agent(model=model, tools=tools, system_prompt=prompt, middleware=middleware,
                                      subagents=None, backend=backend, checkpointer=checkpointer)
            await _run_agent_streamed(agent, task, config, ctx, input_message_id=session_id + "-optimization")
        if assessment:
            if runner.is_stopped(run["id"]):
                raise asyncio.CancelledError()
            if no_change and unchanged_checkout():
                round_record.update(no_change)
                repo.put_run(run)
                sessions.update_session(session_id, status="done")
                if ctx.recorder and ctx.trace_id_self:
                    ctx.recorder.complete_run(ctx.trace_id_self)
                return no_change
            if problem := completion_problem():
                raise IncompleteImprovement(problem)
        if ctx.validation_result is None:
            # A recovered checkpoint can already contain the final change log.
            validate_changes.invoke({})
        if not ctx.change_log_path:
            raise ValueError("进化Agent未产出改动报告")
        sessions.update_session(session_id, status="done", change_log_path=ctx.change_log_path)
        if ctx.recorder and ctx.trace_id_self:
            ctx.recorder.complete_run(ctx.trace_id_self)
        return {"validated": bool(ctx.validation_result.get("passed")) and ctx.code_mutations_since_validate == 0,
                "summary": Path(ctx.change_log_path).read_text(encoding="utf-8"), "usage": {"known": False}}
    except BaseException as exc:
        if isinstance(exc, IncompleteImprovement):
            sessions.update_session(session_id, status="failed")
        if ctx.recorder and ctx.trace_id_self:
            error = str(exc) if isinstance(exc, IncompleteImprovement) else "自动改进中断或失败"
            ctx.recorder.fail_run(ctx.trace_id_self, error)
        raise
    finally:
        if assessment and (ctx.design_doc_path or ctx.change_log_path
                           or baseline_reads != set(round_record.get("baseline_evidence_read", []))):
            round_record["baseline_evidence_read"] = sorted(baseline_reads)
            if round_record.get("outcome") != "no_change":
                if ctx.design_doc_path:
                    _record_design(run, round_record, ctx.design_doc_path)
                if ctx.change_log_path:
                    round_record["summary"] = Path(ctx.change_log_path).read_text(encoding="utf-8")
            repo.put_run(run)
