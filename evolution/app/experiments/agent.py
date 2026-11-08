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


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(["git", *arguments], cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=60)
    if result.returncode:
        raise RuntimeError("候选仓库操作失败：" + result.stderr[-500:])
    return result.stdout.strip()


def improve_candidate(run: dict, round_record: dict, evidence: list[dict]) -> dict:
    """Freeze and prepare a candidate; this function never promotes production."""
    if round_record.get("frozen_candidate"):
        frozen = round_record["frozen_candidate"]
        prepared = clients.prepare_artifact(frozen["commit"])
        return {**frozen, "digest": prepared["digest"]}
    root = settings.db_path.parent / "experiments" / run["id"] / f"round-{round_record['round']}"
    root.parent.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        # A separate clone has a separate index and HEAD; human sessions keep their checkout.
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
        raise
    if not result["validated"]:
        raise ValueError("候选源码校验未通过")
    changed = _git(root, "status", "--porcelain")
    if changed:
        _git(root, "add", "--all", "--", ".", ":(exclude)registry.json")
        _git(root, "-c", "user.name=Writer Evolution", "-c", "user.email=evolution@writer.local", "commit", "-m", f"Experiment {run['id']} round {round_record['round']}")
    commit = _git(root, "rev-parse", "HEAD")
    if run.get("flow_version") == 2 and commit == round_record["base_commit"]:
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
            errors.append(f"{path.relative_to(root)}：{type(exc).__name__}")
    return errors


async def _improve(run: dict, round_record: dict, root: Path, evidence: list[dict]) -> dict:
    from deepagents import create_deep_agent
    from deepagents.backends.filesystem import FilesystemBackend
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI
    from langchain.agents.middleware.types import AgentMiddleware
    from langchain_core.messages import ToolMessage
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from app.common.middleware.no_fs import NoFilesystemToolsMiddleware
    from app.common.model_factory import _ensure_gp_disabled
    from app.evolve import db as sessions
    from app.evolve.ctx import EvolveContext, set_tool_context
    from app.evolve.agent.agent import _run_agent_streamed
    from app.evolve.agent.middleware.flow_guard import FlowGuardMiddleware
    from app.evolve.agent.tools.inspect import make_inspect_tools
    from app.evolve.agent.tools.writers import make_writer_tools
    from app.evolve.agent.tools.flow import make_flow_tools
    from app.experiments import runner
    from app.trace import TraceMiddleware, TraceCallbackHandler

    class ExperimentSourceGuard(AgentMiddleware):
        def wrap_model_call(self, request, handler):
            if runner.is_stopped(run["id"]):
                raise asyncio.CancelledError()
            return handler(request)

        async def awrap_model_call(self, request, handler):
            if runner.is_stopped(run["id"]):
                raise asyncio.CancelledError()
            return await handler(request)

        def _check(self, request):
            if runner.is_stopped(run["id"]):
                raise asyncio.CancelledError()
            arguments = request.tool_call.get("args", {})
            path = arguments.get("file_path", arguments.get("path", ""))
            parts = Path(str(path).replace("\\", "/").lstrip("/")).parts
            if any(part.startswith(".") for part in parts) or (parts and parts[0] in {"registry.json", "contracts", "dataset", "experiments"}):
                return ToolMessage(content="拒绝访问受保护的元数据或实验区域。", tool_call_id=request.tool_call["id"])
            return None

        def wrap_tool_call(self, request, handler):
            denied = self._check(request)
            return denied if denied is not None else handler(request)

        async def awrap_tool_call(self, request, handler):
            denied = self._check(request)
            return denied if denied is not None else await handler(request)

    session_id = round_record["session_id"]
    old_session = sessions.get_session(session_id)
    if old_session and old_session.get("case_id") != "experiment:" + run["id"]:
        db.execute("UPDATE evolve_sessions SET case_id=? WHERE session_id=?", ("experiment:" + run["id"], session_id))
    if old_session and old_session["status"] == "done" and old_session.get("change_log_path"):
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

    @tool
    def validate_changes() -> str:
        """Compile candidate Python without executing imports; Platform does the isolated assembly check."""
        errors = _compile_candidate(root)
        ctx.validation_result = {"passed": not errors, "errors": errors, "env_diffs": []}
        ctx.code_mutations_since_validate = 0
        return json.dumps(ctx.validation_result, ensure_ascii=False)

    backend = FilesystemBackend(root_dir=str(root), virtual_mode=True)
    tools = make_inspect_tools() + make_writer_tools(backend)
    tools += [item for item in make_flow_tools() if item.name != "validate_changes"] + [validate_changes]
    _ensure_gp_disabled()
    credentials = repo.decrypt_credentials(run["private"]["evolver"])
    middleware = [NoFilesystemToolsMiddleware(), ExperimentSourceGuard(), FlowGuardMiddleware()]
    if ctx.recorder and ctx.trace_id_self:
        middleware.append(TraceMiddleware(recorder=ctx.recorder, trace_id=ctx.trace_id_self, agent_name="experiment-evolver"))
    checkpoint_path = root.parent / f"round-{round_record['round']}.sqlite"
    prompt = (
        "你是Writer Harness进化工程师。本实验已授权自动修改，无需逐点人工采纳。"
        "只允许在当前Harness包内修改提示词、技能、工具、中间件和装配代码。"
        "不得修改registry.json、.git、数据集、评测、权限、服务框架；不得寻找终验或其他作品数据。"
        "工具只包含Harness源码读写与校验；不得调用发布。先探查并write_design_doc，再修改、validate_changes、write_change_log。"
        "每次只处理有证据的问题，保留现有质量检查和取证行为。"
        "你收到的证据来自优化集及允许复用的回归题；不要推测或索取独立终验内容。"
    )
    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 200}
    if ctx.recorder and ctx.trace_id_self:
        config["callbacks"] = [TraceCallbackHandler(ctx.recorder, ctx.trace_id_self)]
    try:
        # ChatOpenAI's default HTTP pool is cached across rounds, but each round
        # has its own asyncio.run loop. Close this round's pool before that loop.
        async with DefaultAsyncHttpxClient(timeout=300) as http_client, \
                AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
            model = ChatOpenAI(model=credentials["model"].split(":", 1)[-1], base_url=credentials["base_url"],
                               api_key=credentials["api_key"], temperature=.2, max_tokens=8192,
                               request_timeout=300, max_retries=1, http_async_client=http_client)
            agent = create_deep_agent(model=model, tools=tools, system_prompt=prompt, middleware=middleware,
                                      subagents=None, backend=backend, checkpointer=checkpointer)
            await _run_agent_streamed(agent, "请改进以下优化与回归问题并完整校验收尾：\n" + json.dumps(evidence, ensure_ascii=False), config, ctx,
                                      input_message_id=session_id + "-optimization")
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
    except BaseException:
        if ctx.recorder and ctx.trace_id_self:
            ctx.recorder.fail_run(ctx.trace_id_self, "自动改进中断或失败")
        raise
