"""Relay evaluation model text from an isolated writer over a one-way pipe."""
from __future__ import annotations

import asyncio
import json
import os
import re

import httpx

from contracts.target_tasks import TargetTaskRequest


_PIPE_FRAME_LIMIT = 4096  # Linux PIPE_BUF. A nonblocking write is all-or-nothing.
_DELTA_LIMIT = 550
_BATCH_LIMIT = 15_000
_BATCH_INTERVAL = 0.16
_TRACE_ID = re.compile(r"trace-[0-9a-f]{32}")
_RUN_ID = re.compile(r"[A-Za-z0-9_.-]{1,128}")


def experiment_identity(request: TargetTaskRequest) -> tuple[str, str] | None:
    reference = request.model_snapshot.api_key_ref if request.model_snapshot else None
    match = re.fullmatch(r"experiment:([A-Za-z0-9_-]+):model", reference or "")
    if not match:
        return None
    # Retries have different task directories but share the original sample.
    retry = re.fullmatch(r"(sample-[0-9a-f]{32})-a(?:[2-9]|[1-9][0-9]+)", request.task_id)
    sample = retry[1] if retry and request.task_id == request.attempt_id else request.task_id
    return match[1], sample


def _deltas(kind, content):
    if isinstance(content, str):
        yield "reasoning" if kind == "reasoning_stream" else "text", content
    elif kind == "model_stream" and isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") in {"text", "output_text"} and isinstance(block.get("text"), str):
                yield "text", block["text"]
            elif block.get("type") == "reasoning" and isinstance(block.get("summary"), list):
                for part in block["summary"]:
                    if (isinstance(part, dict) and part.get("type") == "summary_text"
                            and isinstance(part.get("text"), str)):
                        yield "reasoning", part["text"]


def write_model_frame(fd, trace_id, kind, data):
    if kind not in {"reasoning_stream", "model_stream", "model_output"} or not isinstance(data, dict):
        return
    run_id = data.get("run_id")
    if (not isinstance(trace_id, str) or not _TRACE_ID.fullmatch(trace_id)
            or not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id)):
        return

    def write(update):
        line = (json.dumps([trace_id, update], ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        os.write(fd, line)

    try:
        if kind == "model_output":
            write({"op": "end", "run_id": run_id})
        else:
            for channel, text in _deltas(kind, data.get("content")):
                for start in range(0, len(text), _DELTA_LIMIT):
                    write({"op": "delta", "run_id": run_id, "channel": channel,
                           "text": text[start:start + _DELTA_LIMIT]})
    except (OSError, UnicodeError):
        # Slow or disconnected viewers must not stop a model call.
        return


def _decode(line):
    try:
        envelope = json.loads(line)
    except (ValueError, RecursionError):
        return None
    if not isinstance(envelope, list) or len(envelope) != 2:
        return None
    trace_id, update = envelope
    if not isinstance(trace_id, str) or not _TRACE_ID.fullmatch(trace_id) or not isinstance(update, dict):
        return None
    op, run_id = update.get("op"), update.get("run_id")
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        return None
    if op == "end":
        return trace_id, {"op": op, "run_id": run_id}
    channel, text = update.get("channel"), update.get("text")
    if op == "delta" and channel in ("reasoning", "text") and isinstance(text, str) and 0 < len(text) <= _DELTA_LIMIT:
        try:
            text.encode()
        except UnicodeError:
            return None
        return trace_id, {"op": op, "run_id": run_id, "channel": channel, "text": text}
    return None


async def relay_model_frames(pipe, evolution_url, token, identity):
    """Only the parent owns the service token. Child packets cannot choose the destination or sample."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=_PIPE_FRAME_LIMIT)
    transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), pipe)
    try:
        async with httpx.AsyncClient(timeout=1.0, trust_env=False, headers={"X-Notify-Token": token}) as client:
            while first := await reader.readline():
                lines = [first]
                size = len(first)
                deadline = loop.time() + _BATCH_INTERVAL
                try:
                    async with asyncio.timeout_at(deadline):
                        while len(lines) < 24 and size <= _BATCH_LIMIT - _PIPE_FRAME_LIMIT:
                            line = await reader.readline()
                            if not line:
                                break
                            lines.append(line)
                            size += len(line)
                        if not reader.at_eof():
                            await asyncio.sleep(max(0, deadline - loop.time()))
                except TimeoutError:
                    pass
                batches = {}
                for line in lines:
                    decoded = _decode(line)
                    if decoded:
                        trace_id, update = decoded
                        batches.setdefault(trace_id, []).append(update)
                # A task has one current trace. Priority goes to that trace if a batch crosses a stage boundary.
                if batches:
                    trace_id, updates = batches.popitem()
                    try:
                        await client.post(f"{evolution_url}/api/ingestion/model-stream", json={
                            "trace_id": trace_id, "experiment_id": identity[0], "sample_id": identity[1],
                            "updates": updates,
                        })
                    except httpx.HTTPError:
                        continue
    except (OSError, ValueError):
        # Close on malformed framing or gateway failure without touching the job.
        pass
    finally:
        transport.close()
