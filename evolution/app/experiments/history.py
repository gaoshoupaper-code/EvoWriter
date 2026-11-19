"""Read-only tools bound to one assessment and its optimization samples."""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from app.core import db
from app.core.models import TraceLogEvent
from app.experiments import repo
from app.trace_payloads import hydrate_event


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Round(_Strict):
    round_number: int = Field(ge=0)
    section: Literal["samples", "design", "summary", "diff", "startup_check", "error", "regression"]
    offset: int = Field(0, ge=0)


class _Search(_Strict):
    query: str = Field(min_length=1, max_length=160, pattern=r"\S")
    offset: int = Field(0, ge=0)
    limit: int = Field(12, ge=1, le=40)


class _Sample(_Strict):
    round_number: int = Field(ge=0)
    sample_number: int = Field(ge=1)


class _SampleRead(_Sample):
    section: Literal["assignment", "manuscript", "scores", "checks", "error"]
    offset: int = Field(0, ge=0)


class _Trace(_Sample):
    trace_number: int = Field(ge=1)


class _Timeline(_Trace):
    after_sequence: int = Field(0, ge=0)
    limit: int = Field(40, ge=1, le=100)


class _Event(_Trace):
    sequence: int = Field(ge=1)
    offset: int = Field(0, ge=0)


_UNAVAILABLE = "本档案中没有可读取的对应记录。"
_EVENT_TYPES = ("tool_start", "tool_end", "tool_error", "skill_activation", "artifact_revision",
                "middleware_assembly", "middleware_intervention", "run_error", "llm_error")


def _page(value, offset: int) -> dict:
    if value is None:
        return {"error": _UNAVAILABLE}
    content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    end = offset + 6000
    return {"content": content[offset:end], "next_offset": end if end < len(content) else None}


def _regression_drops(run: dict, round_number: int) -> dict:
    """Publish paired average losses using frozen rubric labels, never holdout rows."""
    kind = run["task_type"]
    pairs = defaultdict(dict)
    allowed = run["settings"]["regression_ids"].get(kind, [])
    for sample in run["samples"]:
        if (sample["phase"] == "regression" and sample["round"] == round_number
                and sample["package_id"] in allowed and sample.get("scores")):
            pairs[(sample["package_id"], sample["repeat"])][sample["arm"]] = sample["scores"]["dimensions"]
    drops = {}
    for dimension in run["private"]["rules"][kind]["dimensions"]:
        key = dimension["key"]
        differences = [pair["candidate"][key] - pair["baseline"][key] for pair in pairs.values()
                       if key in pair.get("candidate", {}) and key in pair.get("baseline", {})]
        if differences and sum(differences) < 0:
            drops[key] = round(sum(differences) / len(differences), 6)
    return {"round": round_number, "drops": drops}


def _event_label(event: TraceLogEvent) -> dict:
    return {key: value for key, value in {
        "sequence": event.sequence, "type": event.type, "status": event.status,
        "agent": event.agent_name, "tool": event.tool_name, "skill": event.skill_name,
        "call": event.tool_call_id, "file": (event.artifact or {}).get("logical_key"),
    }.items() if value is not None}


def make_history_tools(run: dict) -> list:
    allowed = set(run["settings"]["optimization_ids"])

    def samples_at(round_number):
        phase = "exploration" if round_number else "baseline"
        return [s for s in run.get("samples", []) if s["round"] == round_number
                and s["phase"] == phase and s["package_id"] in allowed]

    def sample_at(round_number, sample_number):
        samples = samples_at(round_number)
        return samples[sample_number - 1] if sample_number <= len(samples) else None

    def sample_content(sample, section):
        if section == "assignment":
            try:
                package = repo.run_package(run, sample["package_id"])
            except (KeyError, ValueError):
                return None
            return package["input"] if package["purpose"] == "optimization" else None
        if section == "manuscript":
            chapter = sample.get("target", {}).get("chapter")
            name = f"正文/第{chapter:03d}章.md" if chapter else None
            return sample.get("artifacts", {}).get(name)
        return sample.get(section)

    def searchable_sections():
        for record in [{"round": 0}, *run.get("rounds", [])]:
            number = record["round"]
            location = {"round_number": number}
            for key in ("design", "summary", "diff", "startup_check", "error"):
                if record.get(key):
                    yield {**location, "section": key}, record[key]
            if record.get("status") == "regression_failed":
                yield {**location, "section": "regression"}, _regression_drops(run, number)
            for index, sample in enumerate(samples_at(number), 1):
                location_sample = {**location, "sample_number": index, "evidence_ref": sample["id"]}
                for section in ("assignment", "manuscript", "scores", "checks", "error"):
                    content = sample_content(sample, section)
                    if content:
                        yield {**location_sample, "section": section}, content

    def trace_at(round_number, sample_number, trace_number):
        sample = sample_at(round_number, sample_number)
        traces = sample.get("trace_ids", []) if sample else []
        if trace_number > len(traces):
            return None
        trace_id = traces[trace_number - 1]
        row = db.query_one(
            """SELECT run_snapshot_json,workspace_id,thread_id,run_purpose,integrity_status
               FROM runs WHERE trace_id=? AND service='executor'""", (trace_id,),
        )
        if not row:
            return None
        snapshot = json.loads(row["run_snapshot_json"])
        if not isinstance(snapshot, dict):
            return None
        task_id = sample["task_id"]
        if "target_task" in snapshot:
            recorded = snapshot["target_task"]
            if not isinstance(recorded, dict) or recorded.get("task_id") != task_id:
                return None
            if any(recorded[k] != sample[k] for k in ("source_commit", "input_revision", "artifact_digest", "target")
                   if k in recorded and k in sample):
                return None
            return trace_id, {}

        # Completed generation is published only after the executor parent verified every trace
        # in the task's fixed workspace and the runner checked the input and artifact versions.
        checks = sample.get("checks") or {}
        identity = checks.get("identity") or {}
        workspace = f"target-{task_id}"
        if (snapshot.keys() - {"display_context"} or row["integrity_status"] != "incomplete"
                or run["task_type"] != "chapter_text" or sample["id"] != task_id
                or sample.get("generation_status") != "completed" or sample.get("completed") is not True
                or checks.get("complete") is not True or row["run_purpose"] != "optimization"
                or row["workspace_id"] != workspace or row["thread_id"] != f"{workspace}-1"
                or any(not sample.get(k) or identity.get(k) != sample[k] for k in ("target", "source_commit", "input_revision"))):
            return None
        archived = db.query_one(
            """SELECT 1 FROM assessment_entries WHERE assessment_id=? AND category='trace' AND entry_id=?
               AND round_number=? AND version=? AND json_extract(payload, ?)=?""",
            (run["id"], sample["id"], round_number, sample["source_commit"], f"$.trace_ids[{trace_number - 1}]", trace_id),
        )
        if not archived:
            return None
        return trace_id, {"warning": "运行身份快照缺失。以下事件依据父进程核验的本题记录提供，仅供诊断。",
                          "integrity_status": row["integrity_status"]}

    @tool(args_schema=_Strict)
    def list_assessment_history() -> list:
        """列出本次评测的轮次和状态。第 0 轮是原版。"""
        return [{"round": 0, "status": "baseline"}, *[
            {"round": item["round"], "status": item["status"]} for item in run.get("rounds", [])]]

    @tool(args_schema=_Search)
    def search_assessment_history(query: str, offset: int = 0, limit: int = 12) -> dict:
        """按字面词搜索本档案的改法、检查、改进题输入/终稿/评分及失败复测摘要。每个命中字段返回一个片段与详情位置。"""
        query = query.strip()
        items = []
        skipped = 0
        for location, value in searchable_sections():
            content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            index = content.find(query)
            if index == -1:
                continue
            if skipped < offset:
                skipped += 1
                continue
            if len(items) == limit:
                return {"items": items, "next_offset": offset + limit}
            start = max(0, index - 40)
            items.append({**location, "content_offset": start, "excerpt": content[start:index + len(query) + 40]})
        return {"items": items, "next_offset": None}

    @tool(args_schema=_Round)
    def read_assessment_round(round_number: int, section: str, offset: int = 0) -> dict:
        """按轮查改法设计、摘要、diff、启动检查、失败原因、改进题样本列表或复测退分摘要。offset 用于翻页。"""
        record = next((item for item in run.get("rounds", []) if item["round"] == round_number), None)
        if round_number and record is None:
            return {"error": _UNAVAILABLE}
        if section == "samples":
            samples = samples_at(round_number)
            end = offset + 40
            return {"items": [{"sample_number": index, "evidence_ref": sample["id"], "title": sample["package_title"],
                               "repeat": sample["repeat"], "status": sample["status"],
                               "trace_count": len(sample.get("trace_ids", []))}
                              for index, sample in enumerate(samples[offset:end], offset + 1)],
                    "next_offset": end if end < len(samples) else None}
        if section == "regression":
            if record and record["status"] == "regression_failed":
                return _regression_drops(run, round_number)
            return {"error": _UNAVAILABLE}
        return _page((record or {}).get(section), offset)

    @tool(args_schema=_SampleRead)
    def read_improvement_sample(round_number: int, sample_number: int, section: str, offset: int = 0) -> dict:
        """读取改进题的实际题文、最终正文、逐项评分与评语、检查或异常。用 next_offset 续读。"""
        sample = sample_at(round_number, sample_number)
        if sample is None:
            return {"error": _UNAVAILABLE}
        return _page(sample_content(sample, section), offset)

    @tool(args_schema=_Timeline)
    def list_writing_events(round_number: int, sample_number: int, trace_number: int,
                            after_sequence: int = 0, limit: int = 40) -> dict:
        """按实际顺序列本次写作的技能加载、工具与助手调用、稿件快照和错误。trace_number 从 1 起。"""
        binding = trace_at(round_number, sample_number, trace_number)
        if not binding:
            return {"error": _UNAVAILABLE}
        trace_id, notice = binding
        placeholders = ",".join("?" for _ in _EVENT_TYPES)
        rows = db.query_all(
            f"""SELECT payload_json FROM event_payloads WHERE trace_id=? AND sequence>?
                AND type IN ({placeholders}) ORDER BY sequence LIMIT ?""",
            (trace_id, after_sequence, *_EVENT_TYPES, limit + 1),
        )
        events = [TraceLogEvent.model_validate_json(row["payload_json"]) for row in rows[:limit]]
        if any(event.trace_id != trace_id for event in events):
            return {"error": _UNAVAILABLE}
        return {"items": [_event_label(event) for event in events],
                "next_sequence": events[-1].sequence if len(rows) > limit else None, **notice}

    @tool(args_schema=_Event)
    def read_writing_event(round_number: int, sample_number: int, trace_number: int,
                           sequence: int, offset: int = 0) -> dict:
        """读取所列事件的受控详情。按快照、审稿调用、后续快照的顺序核对稿件和意见。"""
        binding = trace_at(round_number, sample_number, trace_number)
        if not binding:
            return {"error": _UNAVAILABLE}
        trace_id, notice = binding
        row = db.query_one("SELECT payload_json FROM event_payloads WHERE trace_id=? AND sequence=?",
                           (trace_id, sequence))
        if not row:
            return {"error": _UNAVAILABLE}
        stored = TraceLogEvent.model_validate_json(row["payload_json"])
        if stored.type not in _EVENT_TYPES or stored.trace_id != trace_id:
            return {"error": _UNAVAILABLE}
        # Only restore allowed fields linked to this exact event by ingestion.
        fields = {"tool_args", "tool_output", "error"}
        if stored.type == "artifact_revision":
            fields.add("output")
        refs = {field: ref for field, ref in stored.payload_refs.items() if field in fields}
        linked = {}
        if refs:
            links = db.query_all(
                "SELECT field_name,payload_id FROM trace_payload_links WHERE trace_id=? AND event_id=?",
                (trace_id, stored.event_id),
            )
            payloads = {row["field_name"]: row["payload_id"] for row in links}
            linked = {field: ref for field, ref in refs.items() if payloads.get(field) == ref.payload_id}
        event = hydrate_event(stored.model_copy(update={"payload_refs": linked, **dict.fromkeys(refs)}))
        details = {"arguments": event.tool_args, "result": event.tool_output, "error": event.error,
                   "skill": event.skill_name, "intervention": event.intervention}
        unavailable = [field for field in refs if getattr(event, field, None) is None]
        if unavailable:
            details["unavailable_fields"] = unavailable
        if event.type == "artifact_revision":
            details["snapshot"] = event.output
        if event.type == "middleware_assembly":
            details["middleware"] = [item.name for item in event.middleware_stack]
        page = _page({**_event_label(event), **{k: v for k, v in details.items() if v is not None}}, offset)
        return {**page, **notice}

    return [list_assessment_history, search_assessment_history, read_assessment_round, read_improvement_sample,
            list_writing_events, read_writing_event]
