"""Responses summaries reach the live experiment view before model completion."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from langchain_openai import ChatOpenAI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.mark.parametrize("output_version", ["responses/v1", "v0"])
def test_evolver_forwards_public_response_deltas_before_completion(output_version):
    from app.experiments import live_output
    from app.experiments.evolver_output import EvolverOutputCallback

    def frame(kind, sequence, **fields):
        event = {"type": kind, "sequence_number": sequence, **fields}
        return f"event: {kind}\ndata: {json.dumps(event)}\n\n".encode()

    class ResponsesStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield frame("response.output_item.added", 0, output_index=0, item={
                "type": "reasoning", "id": "rs_live", "summary": [], "encrypted_content": "ciphertext",
                "content": [{"type": "reasoning_text", "text": "opaque private reasoning"}],
            })
            yield frame("response.reasoning_summary_text.delta", 1, output_index=0, item_id="rs_live",
                        summary_index=0, delta="核对现有规则。")
            assert [(args[2], args[3]) for args, _ in publish.call_args_list] == [("reasoning", "核对现有规则。")]
            finish.assert_not_called()
            yield frame("response.output_text.delta", 2, output_index=1, item_id="msg_live",
                        content_index=0, delta="下一步比较证据。")
            assert [(args[2], args[3]) for args, _ in publish.call_args_list] == [
                ("reasoning", "核对现有规则。"), ("text", "下一步比较证据。"),
            ]
            finish.assert_not_called()
            yield frame("response.completed", 3, response={
                "id": "resp_live", "created_at": 1, "model": "gpt-6.2-sol", "object": "response",
                "status": "completed", "output": [],
            })

    async def handle(request):
        assert request.url.path == "/v1/responses"
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=ResponsesStream())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            model = ChatOpenAI(model="gpt-6.2-sol", api_key="test-only", base_url="http://test.invalid/v1",
                               use_responses_api=True, output_version=output_version, http_async_client=client)
            async for _ in model.astream_events("合成题目", config={"callbacks": [EvolverOutputCallback("trace-live")]}, version="v2"):
                pass

    with patch.object(live_output, "publish_delta") as publish, patch.object(live_output, "finish_call") as finish:
        asyncio.run(run())
        assert publish.call_count == 2
        trace_id, call_id, *_ = publish.call_args.args
        assert trace_id == "trace-live"
        finish.assert_called_once_with(trace_id, call_id)
