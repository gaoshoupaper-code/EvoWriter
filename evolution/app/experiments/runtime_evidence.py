"""Ground runtime scoring in the traces that produced the submitted chapter."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import PurePosixPath

from contracts.trace import SkillCatalogEntry, TraceLogEvent
from contracts.trace.usage import TraceMetricsAccumulator

from app.core import db
from app.ingestion import importer
from app.ingestion.ingestion import _fetch_trace_content
from app.trace.tool_results import is_successful_tool_end
from app.trace_payloads import hydrate_event


SUMMARY_BYTES = 12000
_EXCERPT_CHARS = 600
_TERMINAL = {"completed", "failed", "cancelled", "interrupted", "step_limit_reached"}
_TOOL_EVENTS = {"tool_start", "tool_end", "tool_error"}
_IDENTITY = ("task_id", "source_commit", "input_revision", "artifact_digest", "target")
_READ_TOOLS = {"read_writing_skill", "read_file", "read"}


class EvidenceUnavailable(ValueError):
    """The saved attempt lacks evidence required for a runtime score."""


def _bound(snapshot: dict, sample: dict) -> bool:
    identity = snapshot.get("target_task")
    return isinstance(identity, dict) and all(
        key in identity and identity[key] == sample.get(key) for key in _IDENTITY
    )


def _trace_row(trace_id: str):
    return db.query_one("SELECT service, status, integrity_status, run_snapshot_json FROM runs WHERE trace_id=?",
                        (trace_id,))


def _recover_trace(trace_id: str, sample: dict) -> None:
    # A full fetch can recover payloads for sequences already present locally.
    row = _trace_row(trace_id)
    if row and (row["service"] != "executor" or row["integrity_status"] == "conflict"):
        return
    if row:
        snapshot = json.loads(row["run_snapshot_json"] or "{}")
        if snapshot.get("target_task") and not _bound(snapshot, sample):
            return
    fetched = _fetch_trace_content(trace_id)
    if fetched is None:
        return
    events, run, payloads = fetched
    if (run.trace_id != trace_id or run.service != "executor" or not _bound(run.run_snapshot, sample)
            or any(event.trace_id != trace_id for event in events)):
        return
    importer.ingest_events(events, run.workspace_id, run_status_hint=run.status,
                           run_summary_hint=run, payload_values=payloads)


def _read_trace(trace_id: str, sample: dict) -> tuple[str, list[TraceLogEvent]]:
    row = _trace_row(trace_id)
    if not row or row["service"] != "executor" or row["status"] not in _TERMINAL:
        raise EvidenceUnavailable(f"Trace {trace_id} 尚无可核对的生成终态")
    if row["integrity_status"] != "verified" or not _bound(json.loads(row["run_snapshot_json"] or "{}"), sample):
        raise EvidenceUnavailable(f"Trace {trace_id} 完整性或本次生成身份未通过核对")
    rows = db.query_all("SELECT payload_json FROM event_payloads WHERE trace_id=? ORDER BY sequence", (trace_id,))
    events = [TraceLogEvent.model_validate_json(row["payload_json"]) for row in rows]
    if not events or any(event.trace_id != trace_id for event in events):
        raise EvidenceUnavailable(f"Trace {trace_id} 缺少对应事件")
    return row["status"], events


def _with_fields(event: TraceLogEvent, *fields: str) -> TraceLogEvent:
    refs = {field: event.payload_refs[field] for field in fields if field in event.payload_refs}
    if not refs:
        return event
    restored = hydrate_event(event.model_copy(update={"payload_refs": refs, **dict.fromkeys(refs)}))
    if any(getattr(restored, field) is None for field in refs):
        raise EvidenceUnavailable(f"Trace {event.trace_id}:{event.sequence} 所需事件内容缺失")
    return restored


def _messages(value):
    if isinstance(value, dict):
        if isinstance(value.get("update"), dict):
            return _messages(value["update"])
        if isinstance(value.get("messages"), list):
            return value["messages"]
    return [value]


def _text(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("content", "text", "update", "messages"):
            if key in value:
                return _text(value[key])
    if isinstance(value, list):
        return "\n".join(filter(None, map(_text, value)))
    return ""


def _tool_succeeded(event: TraceLogEvent) -> bool:
    if not is_successful_tool_end(event):
        return False
    if event.tool_output is None:
        raise EvidenceUnavailable(f"Trace {event.trace_id}:{event.sequence} 缺少工具实际结果")
    failure_prefixes = ("[save_artifact 失败", "[save_artifact 拒绝", "[交审失败]", "[拒绝]", "Error:",
                        "已达到本轮审查上限")
    return not any(
        (isinstance(message, dict) and str(message.get("status", "")).lower() in {"error", "cancelled"})
        or _text(message).startswith(failure_prefixes)
        for message in _messages(event.tool_output)
    )


def _tool_calls(status: str, events: list[TraceLogEvent]):
    open_calls = {}
    outcomes = {}
    attempts = defaultdict(list)
    for event in events:
        if event.type not in _TOOL_EVENTS:
            continue
        if not event.tool_call_id:
            raise EvidenceUnavailable(f"Trace {event.trace_id}:{event.sequence} 缺少工具调用编号")
        key = event.agent_name, event.tool_call_id
        if event.type == "tool_start":
            open_calls[key] = event
            continue
        start = open_calls.pop(key, None)
        if start is None or start.tool_name != event.tool_name or event.status not in _TERMINAL:
            raise EvidenceUnavailable(f"Trace {event.trace_id}:{event.sequence} 无法配对工具开始和终态")
        fields = ("tool_output",) if event.type == "tool_end" and event.status == "completed" else ("error",)
        end = _with_fields(event, *fields)
        success = _tool_succeeded(end)
        attempts[key].append((success, end))
        outcomes[key] = (start, end, success, attempts[key])
    if open_calls:
        cancellation = next((event for event in reversed(events) if event.type == "run_cancelled"
                             and event.status == "cancelled"), None)
        if status != "cancelled" or cancellation is None:
            start = next(iter(open_calls.values()))
            raise EvidenceUnavailable(f"Trace {start.trace_id}:{start.sequence} 的工具调用缺少终态")
        for key, start in open_calls.items():
            outcomes[key] = (start, cancellation, False, attempts[key])
    return list(outcomes.values())


def _ref(event: TraceLogEvent) -> str:
    return f"{event.trace_id}:{event.sequence}"


def _item(event: TraceLogEvent, kind: str, text: str) -> dict:
    return {"ref": _ref(event), "kind": kind, "text": text}


def _excerpt(text: str, limit: int = _EXCERPT_CHARS) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + f"\n[中间省略 {len(text) - 2 * half} 字]\n" + text[-half:]


def _tool_example(start: TraceLogEvent, end: TraceLogEvent, success: bool, attempts) -> dict:
    count = len(attempts)
    if success and count > 1:
        failure = next((event for passed, event in reversed(attempts[:-1]) if not passed), None)
        reason = _text(failure.error or failure.tool_output) if failure else ""
        detail = f"首次执行及重试共 {count} 次，最终成功。{reason}"
        if failure:
            detail += f" 失败出处 {_ref(failure)}。"
        return _item(end, "tool_retry", f"{start.agent_name} 调用 {start.tool_name}。{_excerpt(detail)}")
    if not success:
        reason = _text(end.error or end.tool_output) or end.status
        if end.type == "run_cancelled":
            reason = f"运行取消于 {_ref(end)}。{reason}"
        return _item(start if end.type == "run_cancelled" else end, "tool_failed",
                     f"{start.agent_name} 调用 {start.tool_name} 未成功。{_excerpt(reason)}")
    return _item(end, "tool_succeeded", f"{start.agent_name} 调用 {start.tool_name} 成功。")


def _skill_key(entry: SkillCatalogEntry) -> tuple[str, str, str]:
    return entry.scope, entry.runtime_path, entry.content_hash


def _phrases(text: str) -> set[str]:
    phrases = set(re.findall(r"[a-z][a-z0-9_-]{3,}", text.lower()))
    for phrase in re.findall(r"[\u3400-\u9fff]+", text):
        phrases.update(phrase[index:index + 2] for index in range(len(phrase) - 1))
    return phrases


def _skill_actions(events: list[TraceLogEvent], first_read: int, chapter_path: str):
    action_items = []
    manuscript = ""
    for position, event in enumerate(events):
        if event.type == "artifact_revision" and (event.artifact or {}).get("logical_key", "").lstrip("/") == chapter_path:
            output = _with_fields(event, "output").output
            updated = output.get("content") if isinstance(output, dict) else output
            if not isinstance(updated, str) or not updated.strip():
                raise EvidenceUnavailable(f"Trace {_ref(event)} 缺少正文修订内容")
            if position > first_read and updated != manuscript:
                before, after = manuscript.splitlines(), updated.splitlines()
                first_change = next((i for i, pair in enumerate(zip(before, after)) if pair[0] != pair[1]),
                                    min(len(before), len(after)))
                before, after = before[first_change:], after[first_change:]
                while before and after and before[-1] == after[-1]:
                    before.pop()
                    after.pop()
                old, new = "\n".join(before), "\n".join(after)
                excerpt = f"{chapter_path} 第 {first_change + 1} 行起。改前：{_excerpt(old, 400) or '（空）'}\n"
                excerpt += f"改后：{_excerpt(new, 400) or '（空）'}"
                action_items.append((position, _item(event, "skill_manuscript", excerpt), _phrases(new)))
            manuscript = updated
        if position <= first_read or event.type != "tool_end" or event.tool_name != "task":
            continue
        args = _with_fields(event, "tool_args").tool_args
        if isinstance(args, dict) and (args.get("subagent_type") or args.get("name")) == "review":
            result = _with_fields(event, "tool_output")
            text = _text(result.tool_output)
            action_items.append((position, _item(event, "skill_review", _excerpt(
                f"{event.agent_name} 调用审稿。{text}")), _phrases(text)))
    return action_items


def _skills(events: list[TraceLogEvent], calls, chapter_path: str):
    catalog = set()
    activated = {}
    read_phrases = defaultdict(set)
    read_refs = set()
    read_keys = set()
    required = []
    optional = []
    by_id = {(event.trace_id, event.event_id): event for event in events}
    positions = {_ref(event): position for position, event in enumerate(events)}
    for position, event in enumerate(events):
        if event.type == "skill_catalog":
            entries = []
            for entry in event.skill_catalog:
                key = _skill_key(entry)
                if key not in catalog:
                    catalog.add(key)
                    entries.append(f"{entry.scope} 可用 {entry.name}。入口 {entry.runtime_path}。"
                                   f"版本 {entry.version or '未声明'}。")
            if entries:
                required.append(_item(event, "skill_available", "\n".join(entries)))
        if event.type != "skill_activation":
            continue
        evidence = event.skill_activation or {}
        trigger_id = evidence.get("trigger_event_id") or event.parent_event_id
        trigger = by_id.get((event.trace_id, trigger_id))
        if trigger is None or not event.skill_catalog:
            raise EvidenceUnavailable(f"Trace {_ref(event)} 缺少技能读取的登记或原始调用")
        read_refs.add(_ref(trigger))
        entry = event.skill_catalog[0]
        key = _skill_key(entry)
        if event.status == "completed" and (trigger.type != "tool_end" or trigger.tool_name not in _READ_TOOLS):
            raise EvidenceUnavailable(f"Trace {_ref(event)} 的技能读取结果无法核对")
        if event.status == "completed":
            result = _with_fields(trigger, "tool_output")
            if not _tool_succeeded(result) or not (text := _text(result.tool_output)):
                raise EvidenceUnavailable(f"Trace {_ref(trigger)} 缺少可读的技能正文")
            item = _item(trigger, "skill_read", f"{event.agent_name} 读取 {entry.name}。\n{_excerpt(text)}")
            if key not in read_keys:
                required.append(item)
                read_keys.add(key)
            else:
                optional.append(item)
            times = activated.setdefault(key, [])
            times.append(position)
            read_phrases[key].update(_phrases(text))
        else:
            optional.append(_item(event, "skill_failed", f"{event.agent_name} 读取 {entry.name} 失败。"))

    for start, end, success, _ in calls:
        if not success or end.tool_name not in _READ_TOOLS or _ref(end) in read_refs:
            continue
        args = _with_fields(end, "tool_args").tool_args or _with_fields(start, "tool_args").tool_args
        path = (args.get("file_path") or args.get("path")) if isinstance(args, dict) else None
        normalized = str(PurePosixPath(path)) if isinstance(path, str) else ""
        matches = [key for key in catalog if key[0] == end.agent_name
                   and normalized.startswith(str(PurePosixPath(key[1]).parent) + "/")]
        if end.tool_name == "read_writing_skill" or any(key[1] == normalized for key in matches):
            if not matches or any(key[1] == normalized for key in matches):
                raise EvidenceUnavailable(f"Trace {_ref(end)} 缺少对应的技能读取登记")
            key = matches[0]
            text = _text(end.tool_output)
            if not text:
                raise EvidenceUnavailable(f"Trace {_ref(end)} 缺少已读的技能资料")
            item = _item(end, "skill_reference", f"{normalized}\n{_excerpt(text)}")
            (optional if key in read_keys else required).append(item)
            read_keys.add(key)
            activated.setdefault(key, []).append(positions[_ref(end)])
            read_phrases[key].update(_phrases(text))

    if not activated:
        end = events[-1]
        required.append(_item(end, "skill_not_read", "已核对本次交稿的完整运行记录，未见成功读取 Skill。"))
    else:
        first_read = min(min(positions) for positions in activated.values())
        actions = _skill_actions(events, first_read, chapter_path)
        required_actions = set()
        for key, times in activated.items():
            for read in {min(times), max(times)}:
                for kind in ("skill_manuscript", "skill_review"):
                    matches = [(item, phrases) for position, item, phrases in actions
                               if position > read and item["kind"] == kind]
                    if not matches:
                        continue
                    chosen = [matches[-1], matches[0]]
                    if len(matches) > 2:
                        # Substitute stronger middle evidence without taking another slot.
                        terms = read_phrases[key]
                        relevance = [len(terms & phrases) for _, phrases in matches]
                        middle = max(range(1, len(matches) - 1), key=relevance.__getitem__)
                        weakest = 0 if relevance[-1] < relevance[0] else 1
                        if relevance[middle] > (relevance[-1] if weakest == 0 else relevance[0]):
                            chosen[weakest] = matches[middle]
                    required_actions.update(item["ref"] for item, _ in chosen)
        for _, action, _ in actions:
            (required if action["ref"] in required_actions else optional).append(action)
        if not required_actions:
            required.append(_item(events[-1], "skill_no_followup", "读取后未记录新的正文修订或审稿结果。"))
    return bool(activated), required, optional


def _summary(events: list[TraceLogEvent], calls, chapter_path: str) -> dict:
    used, required, optional = _skills(events, calls, chapter_path)
    examples = [_tool_example(*call) for call in calls]
    known_refs = {item["ref"]: item for item in [*required, *optional]}
    representatives = set()
    represented_kinds = set()
    important = []
    for example, (start, _, _, _) in zip(examples, calls):
        kind = example["kind"]
        if example["ref"] in known_refs:
            if kind != "tool_succeeded":
                known_refs[example["ref"]]["text"] += "\n" + example["text"]
            continue
        category = kind, start.tool_name
        if kind != "tool_succeeded" and kind not in represented_kinds:
            represented_kinds.add(kind)
            representatives.add(category)
            required.append(example)
        elif kind != "tool_succeeded" and category not in representatives:
            representatives.add(category)
            important.append(example)
        else:
            optional.append(example)
    optional = important + optional
    result = {"skill_status": "read" if used else "not_read", "evidence": required,
              "omitted": dict(Counter(item["kind"] for item in optional))}
    remaining = SUMMARY_BYTES - len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    if remaining < 0:
        raise EvidenceUnavailable("运行摘要预算内无法保留全部必要的 Skill 证据")
    for item in optional:
        kind = item["kind"]
        count = result["omitted"][kind]
        size = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
        size += (2 if required else 0) + len(str(count - 1)) - len(str(count))
        if size <= remaining:
            required.append(item)
            result["omitted"][kind] = count - 1
            remaining -= size
    positions = {_ref(event): position for position, event in enumerate(events)}
    result["evidence"].sort(key=lambda item: positions[item["ref"]])
    result["omitted"] = {kind: count for kind, count in result["omitted"].items() if count}
    return result


def _collect(trace_ids: list[str], sample: dict) -> dict:
    calls, events = [], []
    metrics = TraceMetricsAccumulator()
    for trace_id in trace_ids:
        status, trace_events = _read_trace(trace_id, sample)
        calls.extend(_tool_calls(status, trace_events))
        events.extend(trace_events)
        for event in trace_events:
            if event.type in {"llm_end", "llm_error"} and event.usage is None and "output" in event.payload_refs:
                event = _with_fields(event, "output")
            metrics.add_event(event)
    if not calls:
        raise EvidenceUnavailable("正文已交稿，但缺少本次工具调用记录")
    usage = metrics.snapshot(terminal=True)
    if (usage.input_tokens is None or usage.output_tokens is None or usage.input_partial
            or usage.output_partial or usage.total_partial):
        raise EvidenceUnavailable("本次生成的 Token 用量不完整")
    tokens = usage.input_tokens + usage.output_tokens
    if tokens == 0 and sample.get("arm") == "baseline":
        raise EvidenceUnavailable("原版的计分 Token 用量为零")
    chapter = sample["target"]["chapter"]
    return {"tools": {"successes": sum(call[2] for call in calls), "total": len(calls)}, "tokens": tokens,
            "summary": _summary(events, calls, f"正文/第{chapter:03d}章.md")}


def collect(sample: dict) -> dict:
    """Read evidence, then retry from the same executor attempt if the copy needs recovery."""
    trace_ids = list(dict.fromkeys(sample.get("trace_ids") or []))
    if not trace_ids:
        raise EvidenceUnavailable("正文已交稿，但缺少本次成功生成的 Trace")
    try:
        return _collect(trace_ids, sample)
    except EvidenceUnavailable:
        for trace_id in trace_ids:
            _recover_trace(trace_id, sample)
        return _collect(trace_ids, sample)
