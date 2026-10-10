import unittest
from types import SimpleNamespace

from langchain_core.messages import AIMessage, AIMessageChunk

from app.domains.writing.deepseek_thinking import (
    DeepSeekThinkingChatModel,
    ReasoningSidecarStore,
    ReasoningStreamChatModel,
)
from app.domains.writing.models import build_writer_model, parse_writer_model


class WriterModelTest(unittest.TestCase):
    def test_parse_writer_model_defaults_to_openai_provider(self) -> None:
        self.assertEqual(parse_writer_model("gpt-4o-mini"), ("openai", "gpt-4o-mini"))

    def test_parse_writer_model_detects_bare_deepseek_model_names(self) -> None:
        self.assertEqual(parse_writer_model("deepseek-v4-pro"), ("deepseek", "deepseek-v4-pro"))

    def test_parse_writer_model_rejects_incomplete_provider_prefix(self) -> None:
        for raw_model in ("deepseek:", ":deepseek-chat"):
            with self.subTest(raw_model=raw_model):
                with self.assertRaises(ValueError):
                    parse_writer_model(raw_model)

    def test_build_writer_model_uses_deepseek_adapter_for_prefixed_model(self) -> None:
        model = build_writer_model(
            SimpleNamespace(
                writer_model="deepseek:deepseek-chat",
                writer_temperature=None,
                writer_top_p=None,
                openai_api_key="test-key",
                openai_base_url="https://api.deepseek.com",
            )
        )

        self.assertIsInstance(model, DeepSeekThinkingChatModel)
        self.assertEqual(model.model_name, "deepseek-chat")
        self.assertEqual(model.extra_body, {"thinking": {"type": "enabled"}})

    def test_build_writer_model_uses_deepseek_adapter_for_bare_model(self) -> None:
        model = build_writer_model(
            SimpleNamespace(
                writer_model="deepseek-v4-pro",
                writer_temperature=None,
                writer_top_p=None,
                openai_api_key="test-key",
                openai_base_url="https://api.deepseek.com",
            )
        )

        self.assertIsInstance(model, DeepSeekThinkingChatModel)
        self.assertEqual(model.model_name, "deepseek-v4-pro")
        self.assertEqual(model.extra_body, {"thinking": {"type": "enabled"}})

    def test_build_writer_model_omits_thinking_for_non_deepseek_models(self) -> None:
        model = build_writer_model(
            SimpleNamespace(
                writer_model="openai:gpt-4o-mini",
                writer_temperature=None,
                writer_top_p=None,
                openai_api_key="test-key",
                openai_base_url="https://api.openai.com/v1",
            )
        )

        self.assertEqual(model.model_name, "gpt-4o-mini")
        self.assertIsNone(model.extra_body)

    # FR-001（REQ-20261010-182114）：openai 路径必须用思考流透传适配器。
    # GLM 等 OpenAI 兼容模型在 delta 上返回 reasoning_content，langchain 默认
    # 转换会丢弃——线上实测透传为 0，WritingEventSink 抓不到思考链。
    def test_build_writer_model_uses_reasoning_passthrough_for_openai_provider(self) -> None:
        for raw_model in ("glm-5.3", "openai:gpt-4o-mini", "qwen3.8-max-preview"):
            with self.subTest(raw_model=raw_model):
                model = build_writer_model(
                    SimpleNamespace(
                        writer_model=raw_model,
                        writer_temperature=None,
                        writer_top_p=None,
                        openai_api_key="test-key",
                        openai_base_url="https://api.example.com/v1",
                    )
                )
                self.assertIsInstance(model, ReasoningStreamChatModel)
                self.assertIsNone(model.extra_body)

    def test_reasoning_stream_model_passes_through_reasoning_delta(self) -> None:
        model = ReasoningStreamChatModel(model="glm-5.3", api_key="test-key", stream_usage=False)

        chunk = model._convert_chunk_to_generation_chunk(
            {"choices": [{"delta": {"role": "assistant", "reasoning_content": "Let me think"}}]},
            AIMessageChunk,
            None,
        )

        self.assertIsNotNone(chunk)
        self.assertEqual(chunk.message.additional_kwargs["reasoning_content"], "Let me think")

    def test_reasoning_stream_model_keeps_plain_content_chunk_intact(self) -> None:
        model = ReasoningStreamChatModel(model="glm-5.3", api_key="test-key", stream_usage=False)

        chunk = model._convert_chunk_to_generation_chunk(
            {"choices": [{"delta": {"role": "assistant", "content": "hello"}}]},
            AIMessageChunk,
            None,
        )

        self.assertIsNotNone(chunk)
        self.assertEqual(chunk.message.content, "hello")
        self.assertNotIn("reasoning_content", chunk.message.additional_kwargs)

    def test_reasoning_stream_model_handles_content_and_reasoning_in_same_delta(self) -> None:
        model = ReasoningStreamChatModel(model="glm-5.3", api_key="test-key", stream_usage=False)

        chunk = model._convert_chunk_to_generation_chunk(
            {"choices": [{"delta": {"role": "assistant", "content": "答", "reasoning_content": "想"}}]},
            AIMessageChunk,
            None,
        )

        self.assertIsNotNone(chunk)
        self.assertEqual(chunk.message.content, "答")
        self.assertEqual(chunk.message.additional_kwargs["reasoning_content"], "想")

    def test_reasoning_stream_model_empty_choices_chunk_yields_no_reasoning(self) -> None:
        model = ReasoningStreamChatModel(model="glm-5.3", api_key="test-key", stream_usage=False)

        chunk = model._convert_chunk_to_generation_chunk(
            {"choices": []},
            AIMessageChunk,
            None,
        )

        # 基类对空 choices 返回空 chunk（非 None）：透传层不得注入 reasoning
        self.assertIsNotNone(chunk)
        self.assertNotIn("reasoning_content", chunk.message.additional_kwargs)

    def test_build_writer_model_disables_sdk_retries_explicitly(self) -> None:
        # CON-003/DEC-003：SDK 内部重试必须显式关闭，重试预算由 WriterRetryController 统一持有。
        # 线上 18 分钟等待的根因之一是此处未显式设置，导致 SDK 默认 max_retries 与外层相乘。
        model = build_writer_model(
            SimpleNamespace(
                writer_model="openai:gpt-4o-mini",
                writer_temperature=None,
                writer_top_p=None,
                openai_api_key="test-key",
                openai_base_url="https://api.openai.com/v1",
            )
        )
        self.assertEqual(model.max_retries, 0)
        # 300s：非流式调用首字节=整篇生成完成，GLM-5.3 长文单次常超 120s
        # （benchmark OpenAITimeoutError 连环失败根因），与 evolution 侧先例对齐。
        self.assertEqual(model.request_timeout, 300)


class DeepSeekThinkingModelTest(unittest.TestCase):
    def test_sidecar_saves_reasoning_by_tool_call_id(self) -> None:
        store = ReasoningSidecarStore()
        message = AIMessage(
            content="",
            additional_kwargs={"reasoning_content": "need a tool"},
            tool_calls=[{"name": "lookup", "args": {}, "id": "call_1"}],
        )

        store.save(message)

        stripped_message = AIMessage(
            content="",
            tool_calls=[{"name": "lookup", "args": {}, "id": "call_1"}],
        )
        self.assertEqual(store.reasoning_for_message(stripped_message), "need a tool")

    def test_sidecar_ignores_reasoning_without_tool_calls(self) -> None:
        store = ReasoningSidecarStore()
        store.save(AIMessage(content="done", additional_kwargs={"reasoning_content": "private"}))

        self.assertIsNone(store.reasoning_for_message(AIMessage(content="done")))

    def test_chat_result_preserves_deepseek_reasoning(self) -> None:
        model = DeepSeekThinkingChatModel(
            model="deepseek-chat",
            api_key="test-key",
            base_url="https://api.deepseek.com",
            extra_body={"thinking": {"type": "enabled"}},
            stream_usage=False,
        )

        result = model._create_chat_result(
            {
                "model": "deepseek-chat",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "reasoning_content": "need a tool",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "lookup", "arguments": "{}"},
                                }
                            ],
                        },
                    }
                ],
            }
        )

        message = result.generations[0].message
        self.assertEqual(message.additional_kwargs["reasoning_content"], "need a tool")
        stripped_payload = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }
            ],
        }
        self.assertEqual(model._hydrate_payload_message(stripped_payload)["reasoning_content"], "need a tool")

    def test_request_payload_preserves_existing_reasoning_from_messages(self) -> None:
        model = DeepSeekThinkingChatModel(
            model="deepseek-chat",
            api_key="test-key",
            base_url="https://api.deepseek.com",
            extra_body={"thinking": {"type": "enabled"}},
            stream_usage=False,
        )
        message = AIMessage(
            content="",
            additional_kwargs={"reasoning_content": "need a tool"},
            tool_calls=[{"name": "lookup", "args": {}, "id": "call_1"}],
        )

        payload = model._get_request_payload([message])

        self.assertEqual(payload["messages"][0]["reasoning_content"], "need a tool")

    def test_stream_chunk_preserves_reasoning_delta(self) -> None:
        model = DeepSeekThinkingChatModel(
            model="deepseek-chat",
            api_key="test-key",
            base_url="https://api.deepseek.com",
            extra_body={"thinking": {"type": "enabled"}},
            stream_usage=False,
        )

        chunk = model._convert_chunk_to_generation_chunk(
            {"choices": [{"delta": {"role": "assistant", "reasoning_content": "step"}}]},
            AIMessageChunk,
            None,
        )

        self.assertIsNotNone(chunk)
        self.assertEqual(chunk.message.additional_kwargs["reasoning_content"], "step")


if __name__ == "__main__":
    unittest.main()
