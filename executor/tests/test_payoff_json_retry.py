"""结构化检查的格式恢复有界，不把坏输出当成有效 JSON。"""
import json

import pytest

from app.domains.writing.payoff_validation import ValidationEngine


@pytest.mark.parametrize("partial", ["", '{"entries":[{"name":"unfinished', '```json\n{"entries":'])
def test_incomplete_json_retries_same_request_once(partial):
    messages = [{"role": "user", "content": "Extract entries"}]
    calls = []
    responses = iter([partial, '{"entries":[]}'])

    def call(actual_messages, purpose):
        calls.append((actual_messages, purpose))
        return next(responses)

    assert ValidationEngine(call)._json(messages, "payoff_catalog") == {"entries": []}
    assert calls == [(messages, "payoff_catalog"), (messages, "payoff_catalog")]


def test_repeated_incomplete_json_stops_after_second_response():
    calls = []

    def call(messages, purpose):
        calls.append(purpose)
        return '{"entries":'

    with pytest.raises(json.JSONDecodeError):
        ValidationEngine(call)._json([], "payoff_catalog")
    assert calls == ["payoff_catalog", "payoff_catalog"]


def test_complete_json_does_not_retry():
    calls = []

    def call(messages, purpose):
        calls.append(purpose)
        return '```json\n{"entries":[]}\n```'

    assert ValidationEngine(call)._json([], "payoff_catalog") == {"entries": []}
    assert calls == ["payoff_catalog"]


def test_non_object_json_is_rejected_without_retry():
    calls = []

    def call(messages, purpose):
        calls.append(purpose)
        return "[]"

    with pytest.raises(ValueError, match="JSON对象"):
        ValidationEngine(call)._json([], "payoff_catalog")
    assert calls == ["payoff_catalog"]
