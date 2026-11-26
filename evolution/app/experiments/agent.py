"""Noninteractive improvement in a private checkout, with optimization-only evidence."""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Literal

from openai import APIConnectionError, APIStatusError, DefaultAsyncHttpxClient

from app.core import db
from app.core.settings import settings
from app.experiments import clients, repo


class IncompleteImprovement(ValueError):
    """The chapter evolver ended without a valid decision or candidate."""


_MAX_COMPLETION_NUDGES = 3
_FEEDBACK_SECTIONS = {"manuscript", "scores"}


def _failure_review_problem(record: dict, *, stop: bool = False) -> str | None:
    feedback = record.get("failure_feedback")
    if not feedback:
        return None
    review = record.get("failure_review", {})
    if review.get("round") != feedback["round"]:
        return "请先核对上轮失败稿和原稿的正文及评分，再调用 record_failure_review 提交决策。"
    if stop and review["decision"] != "stop":
        return "以无改动结束前，请先提交停止决策。"
    if not stop and review["decision"] == "stop":
        return "已提交停止决策，请调用 finish_without_changes 完成本轮。"
    return None


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
        if problem := _failure_review_problem(round_record):
            raise IncompleteImprovement(problem)
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
        if problem := _failure_review_problem(round_record, stop=True):
            raise IncompleteImprovement(problem)
        return result
    if problem := _failure_review_problem(round_record):
        raise IncompleteImprovement(problem)
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
            "\n\n【全面排查】\n"
            "你负责正文 Agent 从题目到单章交稿的整体效果。首轮先查第 0 轮原版。"
            "用 read_assessment_round（section=samples）列出样本，先扫交稿情况、正文各项评分和运行开销。"
            "结合题面与终稿核对重要问题，再查相关写作 Trace，判断问题出现在取材、起稿、审稿、修订还是交稿。"
            "读取 architecture.json，核对实际装配的提示词、技能、工具、中间件与子 Agent。"
            "遇到题意不清、判分分歧或生成波动，先核查现有证据。优先追查影响大、改动可归因的问题。"
            "\n\n【提炼通用改法】\n"
            "从具体问题找出可复现的触发条件、可能失效的环节和应做的动作。"
            "换掉作品、人物和情节后，同一条件应仍可识别，同一修正应仍可执行。"
            "个案细节留在证据和说明中。设计写明适用条件、具体改法、一个适用例和一个不适用例。"
            "假设例用于核对边界。引用改进题时，将样本列表返回的 evidence_ref 原样写入设计条目。"
            "\n\n【优先改写作工具说明】\n"
            "段落写得不好，先圈出具体句子。用 list_writing_events 找到相关调用。"
            "再用 read_writing_event 核对已有写法、正文参数和回执。对照起稿与审修，确认问题在哪一步出现。"
            "问题出在选工具或下笔时，优先改写作工具说明。一个证据明确的差句就可以试。"
            "按句子的用途和实际调用找工具。动作 write_action、语言 write_dialogue、感官 write_sensory、"
            "反应 write_reaction、叙述 write_narration。"
            "先用 read_source 读 tools/ 下对应的文件。用 edit_source 只改实际传给模型的工具描述。"
            "工具说明写清何时要留意、调用前想清什么、最后选哪个细节落笔。"
            "有 writing_intent 参数时，让执笔 Agent 先用短语记下本句的写法，再填正文。"
            "例如动作在空间上接不上，给 write_action 补上落笔前的判断。先想清人物的体态和朝向。"
            "再核对人物与人或物的相对位置、是否够得着，以及动作的落点。只把影响理解的细节写进这一句。"
            "复测时核对同类句子是否更容易看懂，也留意是否堆了无用细节。"
            "\n\n【小步试改】\n"
            "能用真实成稿或 Trace 把问题定位到实际装配的某个环节，就可以试验有依据的通用假设。"
            "单次问题也可以试。写明已观察的事实、待验证的原因、拟改资产和可回退的改法。"
            "每轮聚焦一个主要假设。同一机制涉及多项资产时可以联动修改。"
            "事先说明复测应看到什么改善、什么结果会推翻假设，以及可能出现的退步和开销变化。"
            "validate_changes 检查候选源码。write_change_log 记录实际改动和待复测的预期。"
            "正文和开销以实验系统的同题复测结果为准。"
            "\n\n【用反馈决定下一轮】\n"
            "从候选继续时核对任务指定轮次，并读第 0 轮原版的同题证据。"
            "收到失败轮资料后，核对指定同题两稿的正文与逐项评分。"
            "把上轮预期与实际正文、各项分差和开销对照。保留有效改进，找出仍有的缺口和新增退步。"
            "用 record_failure_review 记录继续、换方向或停止。重试同一方向须给出新证据。"
            "只能查看本次档案。独立复测题只提供掉分项和幅度。跨题收益须有独立证据。"
            "若查不到可复用的缺口，或无法提出能复测的改法，用 finish_without_changes 如实结束。"
            "首轮引用实读原版样本。失败复盘轮先提交停止决策，再引用该决策中的同题样本。"
            "保留干净工作区，禁止占位改动或虚构报告。"
        )
    return prompt


def _evolver_tools(backend, run: dict, ctx=None, *, on_baseline_evidence=None, on_sample_read=None,
                   finish_no_change=None):
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
            """提交无改动理由。首轮引用实读原版，失败复盘轮引用停止决策中的同题样本。工作区须无改动。"""
            if finish_no_change is None:
                raise RuntimeError("Finishing requires an experiment checkout")
            return finish_no_change(reason, evidence_refs)

        tools.extend(make_history_tools(run, on_baseline_evidence=on_baseline_evidence, on_sample_read=on_sample_read))
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
    from langchain_core.tools import tool
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from app.common.middleware.no_fs import NoFilesystemToolsMiddleware
    from app.common.model_factory import _ensure_gp_disabled
    from app.evolve import db as sessions
    from app.evolve.ctx import EvolveContext, set_tool_context
    from app.evolve.agent.agent import _run_agent_streamed
    from app.evolve.agent.middleware.flow_guard import FINALIZE_ONLY_TOOLS, FlowGuardMiddleware
    from app.experiments import evaluation, runner
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
            if feedback and tool_name in FINALIZE_ONLY_TOOLS:
                problem = _failure_review_problem(round_record)
                if not problem and tool_name == "write_design_doc":
                    selected = set(round_record["failure_review"]["evidence_refs"])
                    candidate_refs = selected & {case["candidate_id"] for case in feedback["cases"]}
                    changes = arguments.get("changes")
                    if not isinstance(changes, list) or not any(
                            any(isinstance(ref, str) and ref in candidate_refs for ref in change["evidence_ref"])
                            for change in changes if isinstance(change, dict) and isinstance(change.get("evidence_ref"), list)):
                        problem = "改动方案须引用已核对的上轮失败样本。"
                elif not problem and not ctx.design_doc_path:
                    problem = "请先用 write_design_doc 提交含失败证据的本轮方案。"
                if problem:
                    return ToolMessage(content=problem, status="error", tool_call_id=request.tool_call["id"])
            return None

        def wrap_tool_call(self, request, handler):
            denied = self._check(request)
            return denied if denied is not None else handler(request)

        async def awrap_tool_call(self, request, handler):
            denied = self._check(request)
            return denied if denied is not None else await handler(request)

    assessment = bool(run.get("assessment_version"))
    feedback = round_record.get("failure_feedback")
    if assessment and round_record.get("outcome") == "no_change":
        if problem := _failure_review_problem(round_record, stop=True):
            raise IncompleteImprovement(problem)
        return {key: round_record[key] for key in ("outcome", "summary", "evidence_samples")}

    session_id = round_record["session_id"]
    old_session = sessions.get_session(session_id)
    if old_session and old_session.get("case_id") != "experiment:" + run["id"]:
        db.execute("UPDATE evolve_sessions SET case_id=? WHERE session_id=?", ("experiment:" + run["id"], session_id))
    if old_session and old_session["status"] == "done" and old_session.get("change_log_path"):
        if problem := _failure_review_problem(round_record):
            raise IncompleteImprovement(problem)
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

    feedback_samples = {}
    for case in (feedback or {}).get("cases", []):
        feedback_samples[case["baseline_id"]] = 0
        feedback_samples[case["candidate_id"]] = feedback["round"]
    failed_manuscripts = {}
    for sample in run["samples"] if feedback else ():
        if feedback_samples.get(sample["id"]) == feedback["round"]:
            path = f"正文/第{sample['target']['chapter']:03d}章.md"
            failed_manuscripts[sample["id"]] = {path: sample["artifacts"][path]}

    def note_feedback_read(round_number, sample_id, section):
        if feedback_samples.get(sample_id) != round_number:
            return
        with repo.run_lock(run["id"]):
            reads = round_record.setdefault("failure_feedback_reads", {})
            sections = set(reads.get(sample_id, []))
            if section not in sections:
                reads[sample_id] = sorted(sections | {section})
                repo.put_run(run)

    @tool
    def record_failure_review(decision: Literal["continue", "change_direction", "stop"], reason: str,
                              evidence_refs: list[str], manuscript_location: str, new_evidence: str = "") -> str:
        """提交上轮失败复盘。manuscript_location 用失败稿路径:行号。continue 须填 new_evidence。"""
        reason, manuscript_location, new_evidence = reason.strip(), manuscript_location.strip(), new_evidence.strip()
        if not reason or not manuscript_location or (decision == "continue" and not new_evidence):
            return "请填写决策理由和正文位置。重试同一方向时请补充新证据。"
        if any(len(field) > 1000 for field in (reason, manuscript_location, new_evidence)):
            return "决策理由、正文位置和新证据每项最多 1000 字。"
        with repo.run_lock(run["id"]):
            refs = set(evidence_refs)
            reads = round_record.get("failure_feedback_reads", {})
            pairs = [{case["baseline_id"], case["candidate_id"]} for case in feedback["cases"]]
            selected = [pair for pair in pairs if pair <= refs]
            if (not pairs or pairs[0] not in selected or set().union(*selected) != refs
                    or any(not _FEEDBACK_SECTIONS.issubset(reads.get(ref, [])) for ref in refs)):
                return "请先读取最差分差题的失败稿与同题原稿，各读正文和评分，并引用完整配对样本。"
            path, separator, _ = manuscript_location.partition(":")
            if not separator or not any(path in files and evaluation._evidence_issue(manuscript_location, files) is None
                                        for ref, files in failed_manuscripts.items() if ref in refs):
                return "请提交所引用失败稿中可定位的行号，格式为正文路径:行号或正文路径:起始行-结束行。"
            if round_record.get("failure_review"):
                return "本轮失败复盘决策已记录。"
            review = {"round": feedback["round"], "decision": decision, "reason": reason,
                      "manuscript_location": manuscript_location, "evidence_refs": sorted(refs),
                      "verified_sections": sorted(_FEEDBACK_SECTIONS)}
            if new_evidence:
                review["new_evidence"] = new_evidence
            round_record["failure_review"] = review
            repo.put_run(run)
        return "已记录本轮失败复盘决策。"

    def unchanged_checkout():
        return (not _git(root, "status", "--porcelain", "--untracked-files=all")
                and _git(root, "rev-parse", "HEAD") == round_record["base_commit"])

    def finish_no_change(reason, evidence_refs):
        nonlocal no_change
        reason = reason.strip()
        refs = list(dict.fromkeys(evidence_refs))
        if not reason or len(reason) > 1000:
            return "请提交 1 到 1000 字的无改动理由。"
        if feedback:
            if problem := _failure_review_problem(round_record, stop=True):
                return problem
            if set(refs) != set(expected := round_record["failure_review"]["evidence_refs"]):
                return "引用与停止决策不一致。请原样引用已核对的样本：" + "、".join(expected) + "。"
        elif not refs or not baseline_reads:
            return "请先读取第 0 轮原版评分、终稿、检查结果或写作记录，再引用实际读过的样本。"
        elif unknown := sorted(set(refs) - baseline_reads):
            # 长十六进制 ID 易被模型抄漏末位字符；拒绝时给出可复制的完整引用，避免反复重读仍失败。
            return ("evidence_refs 含未核对的引用：" + "、".join(unknown)
                    + "。请原样引用已读样本的完整 evidence_ref：" + "、".join(sorted(baseline_reads)) + "。")
        if not unchanged_checkout():
            return "工作区已有改动，请补齐候选校验和改动报告。"
        no_change = {"outcome": "no_change", "summary": reason, "evidence_samples": refs}
        if feedback:
            round_record.update(no_change)
            repo.put_run(run)
        return "已记录无改动结论，可以结束本轮。"

    def completion_problem():
        if problem := _failure_review_problem(round_record, stop=bool(no_change)):
            return problem
        if not feedback and not baseline_reads:
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
        backend, run, ctx, on_baseline_evidence=baseline_reads.add,
        on_sample_read=note_feedback_read if feedback else None, finish_no_change=finish_no_change)
    if feedback:
        tools.append(record_failure_review)
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
        base_round = next((item["round"] for item in run["rounds"]
                           if item.get("source_commit") == round_record["base_commit"] and item.get("report")), 0)
        if base_round:
            task = (f"当前工作区沿用第 {base_round} 轮候选。先查看该轮的改进题样本，找出仍需解决的问题。"
                    "再查看第 0 轮原版证据作为对照。原版样本入口：" + entries)
        else:
            task = "先核查第 0 轮原版样本的实际证据，再决定是否改进。原版样本入口：" + entries
        if feedback:
            task += ("\n改版起点与必查失败轮分别指定。先核对下列失败轮。"
                     "优先读取 cases 首组的原稿与失败稿，各查正文及逐项评分；必要时继续翻页。"
                     "用 record_failure_review 提交继续、换方向或停止的决策，引用同题两稿。"
                     "继续同一方向要说明新证据。提交决策后才能设计、改文件或按停止决策无改动结束。"
                     "复盘记录引用失败稿路径和行号。写入 Harness 的修正须能跨章节复用。"
                     "失败轮资料：" + json.dumps(feedback, ensure_ascii=False))
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
