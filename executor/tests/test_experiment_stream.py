from __future__ import annotations

import asyncio
import json
import multiprocessing as mp
import os
from types import SimpleNamespace

import httpx
import pytest

from app.domains.writing import experiment_stream as stream


TRACE_ID = "trace-" + "a" * 32


def test_experiment_retries_stream_to_the_original_sample():
    sample = "sample-" + "b" * 32
    request = SimpleNamespace(task_id=sample + "-a2", attempt_id=sample + "-a2", model_snapshot=SimpleNamespace(
        api_key_ref="experiment:exp:with:invalid:ref",
    ))
    assert stream.experiment_identity(request) is None
    request.model_snapshot.api_key_ref = "experiment:experiment-example:model"
    assert stream.experiment_identity(request) == ("experiment-example", sample)


def test_writer_stream_frames_exclude_tools_and_opaque_provider_data():
    read_fd, write_fd = os.pipe()
    try:
        stream.write_model_frame(write_fd, TRACE_ID, "reasoning_stream", {"run_id": "a", "content": "思考"})
        stream.write_model_frame(write_fd, TRACE_ID, "model_stream", {"run_id": "b", "content": [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "摘要"}],
             "encrypted_content": "private", "content": "private"},
            {"type": "reasoning_text", "text": "private"},
            {"type": "text", "text": "可见说明"},
        ]})
        stream.write_model_frame(write_fd, TRACE_ID, "tool_output", {"run_id": "b", "content": "private"})
        stream.write_model_frame(write_fd, TRACE_ID, "model_output", {"run_id": "b", "tool_calls": ["private"]})
    finally:
        os.close(write_fd)
    with os.fdopen(read_fd, "rb") as frames:
        updates = [stream._decode(line) for line in frames]
    assert updates == [
        (TRACE_ID, {"op": "delta", "run_id": "a", "channel": "reasoning", "text": "思考"}),
        (TRACE_ID, {"op": "delta", "run_id": "b", "channel": "reasoning", "text": "摘要"}),
        (TRACE_ID, {"op": "delta", "run_id": "b", "channel": "text", "text": "可见说明"}),
        (TRACE_ID, {"op": "end", "run_id": "b"}),
    ]


def _produce_until_released(pipe, finish):
    with pipe:
        os.set_blocking(pipe.fileno(), False)
        stream.write_model_frame(pipe.fileno(), TRACE_ID, "reasoning_stream", {"run_id": "call-1", "content": "先核对章纲"})
        if finish.wait(timeout=8):
            stream.write_model_frame(pipe.fileno(), TRACE_ID, "model_output", {"run_id": "call-1"})


@pytest.mark.skipif(os.name != "posix", reason="isolated target workers use a Linux pipe")
@pytest.mark.asyncio
async def test_parent_relays_reasoning_before_spawned_worker_ends(monkeypatch):
    context = mp.get_context("spawn")
    read_pipe, write_pipe = context.Pipe(duplex=False)
    finish = context.Event()
    delivered = asyncio.Event()
    requests = []

    async def receive(request):
        requests.append((request.url.path, request.headers["X-Notify-Token"], json.loads(request.content)))
        delivered.set()
        return httpx.Response(204)

    client_type = httpx.AsyncClient
    monkeypatch.setattr(stream.httpx, "AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(receive), **kwargs))
    process = context.Process(target=_produce_until_released, args=(write_pipe, finish))
    process.start()
    write_pipe.close()
    relay = asyncio.create_task(stream.relay_model_frames(read_pipe, "http://evolution", "test-token", ("exp", "sample")))
    try:
        await asyncio.wait_for(delivered.wait(), timeout=6)
        assert process.is_alive()
        assert requests == [("/api/ingestion/model-stream", "test-token", {
            "trace_id": TRACE_ID, "experiment_id": "exp", "sample_id": "sample", "updates": [
                {"op": "delta", "run_id": "call-1", "channel": "reasoning", "text": "先核对章纲"},
            ],
        })]
        finish.set()
        await asyncio.wait_for(relay, timeout=5)
        assert requests[-1][2]["updates"] == [{"op": "end", "run_id": "call-1"}]
    finally:
        finish.set()
        relay.cancel()
        await asyncio.gather(relay, return_exceptions=True)
        read_pipe.close()
        process.join(timeout=2)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
