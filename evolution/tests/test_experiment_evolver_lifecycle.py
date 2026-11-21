"""Experiment rounds own their HTTP pool and release it before closing the loop."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_experiments as fixtures


class EvolverHttpLifecycleTest(fixtures.DatabaseTest):
    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.clients = []
        self.failure = None
        self.requests = []
        self.outputs = []
        requests = self.requests

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(handler):
                request = json.loads(handler.rfile.read(int(handler.headers["Content-Length"])))
                requests.append((handler.path, request))
                reply = {
                    "id": "chat-test", "object": "chat.completion", "created": 1,
                    "model": "test-model", "choices": [{"index": 0,
                        "message": {"role": "assistant", "content": "validated"},
                        "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
                if handler.path == "/v1/responses":
                    reply = {"id": "resp-test", "object": "response", "created_at": 1, "model": request["model"],
                        "status": "completed", "output": [
                            {"type": "reasoning", "id": "rs_test", "summary": [
                                {"type": "summary_text", "text": "The model checked the existing rule."}]},
                            {"type": "message", "id": "msg_test", "status": "completed", "role": "assistant",
                                "content": [{"type": "output_text", "text": "validated", "annotations": []}]},
                        ]}
                payload = json.dumps(reply).encode()
                handler.send_response(200)
                handler.send_header("Content-Type", "application/json")
                handler.send_header("Content-Length", str(len(payload)))
                handler.end_headers()
                handler.wfile.write(payload)
                handler.wfile.flush()

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)
        self.directory.cleanup()
        super().tearDown()

    def improve(self, round_number, *, model_name="test-model"):
        from app.experiments.agent import _improve

        async def stream(model, _input, _config, ctx, **_kwargs):
            self.clients.append(model.root_async_client._client)
            message = await model.ainvoke("exercise the real HTTP pool")
            self.assertEqual(message.text, "validated")
            self.outputs.append(message.content)
            if self.failure:
                raise self.failure
            ctx.validation_result = {"passed": True}
            report = self.root / f"change-{round_number}.md"
            report.write_text("validated", encoding="utf-8")
            ctx.change_log_path = str(report)

        record = {"round": round_number, "session_id": f"http-lifecycle-r{round_number}"}
        credentials = {"model": model_name, "api_key": "test-only",
                       "base_url": f"http://127.0.0.1:{self.server.server_port}/v1"}
        with patch("app.experiments.repo.decrypt_credentials", return_value=credentials), \
             patch("app.experiments.runner.recorder", return_value=None), \
             patch("deepagents.create_deep_agent", side_effect=lambda **kwargs: kwargs["model"]), \
             patch("app.evolve.agent.agent._run_agent_streamed", side_effect=stream):
            return asyncio.run(_improve({"id": "experiment-http-lifecycle", "private": {"evolver": {}}},
                                        record, self.root, []))

    def test_rounds_use_distinct_pools_closed_inside_their_event_loops(self):
        self.assertTrue(self.improve(1)["validated"])
        self.assertTrue(self.improve(2)["validated"])
        self.assertIsNot(self.clients[0], self.clients[1])
        self.assertTrue(all(client.is_closed for client in self.clients))

    def test_summary_opt_in_follows_existing_responses_routing(self):
        self.assertTrue(self.improve(1, model_name="gpt-5-pro")["validated"])
        self.assertTrue(self.improve(2)["validated"])
        responses, chat = self.requests
        self.assertEqual(responses[0], "/v1/responses")
        self.assertEqual(responses[1]["reasoning"], {"summary": "auto"})
        from app.trace.trace_middleware import _model_output
        from contracts.trace.payload import PayloadGate
        from langchain_core.messages import AIMessage

        recorded = PayloadGate().prepare(_model_output([AIMessage(content=self.outputs[0])])).canonical_json
        self.assertIn(b"The model checked the existing rule.", recorded)
        self.assertEqual(chat[0], "/v1/chat/completions")
        self.assertNotIn("reasoning", chat[1])

    def test_failure_and_cancellation_release_the_pool_before_the_next_round(self):
        for round_number, error in ((1, RuntimeError("generation failed")), (2, asyncio.CancelledError())):
            with self.subTest(error=type(error).__name__):
                self.failure = error
                with self.assertRaises(type(error)):
                    self.improve(round_number)
                self.assertTrue(self.clients[-1].is_closed)
        self.failure = None
        self.assertTrue(self.improve(3)["validated"])
        self.assertTrue(self.clients[-1].is_closed)
