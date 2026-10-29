from __future__ import annotations

import unittest

from contracts.trace.usage import TraceMetricsAccumulator, metrics_from_nodes, normalize_usage, usage_from_output


def _llm(node_id: str, **values: object) -> dict[str, object]:
    return {"node_id": node_id, "kind": "llm", "status": "completed", **values}


class TraceUsageTest(unittest.TestCase):
    def test_normalization_preserves_zero_and_does_not_invent_partial_total(self) -> None:
        self.assertEqual(normalize_usage({"input_tokens": 0, "output_tokens": 0}), {
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        })
        self.assertEqual(normalize_usage({"output_tokens": 3000}), {
            "input_tokens": None, "output_tokens": 3000, "total_tokens": None,
        })
        self.assertEqual(normalize_usage({"prompt_tokens": 8000, "completion_tokens": 2000})["total_tokens"], 10000)
        self.assertEqual(normalize_usage({"input_tokens": 0, "prompt_tokens": 12, "total_tokens": 7})["input_tokens"], 0)

    def test_partial_fields_are_independent_and_totals_are_derived_per_call(self) -> None:
        metrics = metrics_from_nodes([
            _llm("a", usage={"output_tokens": 3000}),
            _llm("b", usage={"input_tokens": 8000, "output_tokens": 2000}),
        ])
        self.assertEqual((metrics.input_tokens, metrics.output_tokens, metrics.total_tokens), (8000, 5000, 10000))
        self.assertEqual((metrics.input_partial, metrics.output_partial, metrics.total_partial), (True, False, True))

    def test_running_calls_do_not_create_missing_samples_and_unknown_is_not_zero(self) -> None:
        empty = metrics_from_nodes([_llm("running", status="running")])
        self.assertIsNone(empty.total_tokens)
        self.assertFalse(empty.total_partial)
        known_zero = _llm("zero", usage={"input_tokens": 0, "output_tokens": 0}, duration_ms=0)
        metrics = metrics_from_nodes([known_zero, _llm("pending", status="running")])
        self.assertEqual(metrics.total_tokens, 0)
        self.assertEqual(metrics.average_duration_ms, 0)
        self.assertFalse(metrics.total_partial)
        missing = metrics_from_nodes([_llm("missing")])
        self.assertIsNone(missing.input_tokens)
        self.assertIsNone(missing.total_tokens)

    def test_mean_uses_successful_calls_and_deduplicates_nodes(self) -> None:
        first = _llm("first", usage={"total_tokens": 15000}, duration_ms=10000)
        second = _llm("second", usage={"total_tokens": 10000}, duration_ms=30000)
        metrics = metrics_from_nodes([
            first, second, first,
            _llm("missing-duration", usage={"total_tokens": 0}),
            _llm("running", status="running", duration_ms=90000),
            {"node_id": "tool", "kind": "tool", "status": "completed", "duration_ms": 90000},
            {"node_id": "agent", "kind": "agent", "status": "completed", "usage": {"total_tokens": 25000}},
        ])
        self.assertEqual(metrics.total_tokens, 25000)
        self.assertEqual(metrics.average_duration_ms, 20000)

    def test_failed_and_cancelled_llm_usage_is_retained_without_averaging(self) -> None:
        metrics = metrics_from_nodes([
            _llm("ok", usage={"total_tokens": 100}, duration_ms=10000),
            {"node_id": "llm:failure", "kind": "error", "status": "failed", "duration_ms": 60000, "usage": {"total_tokens": 25}},
            _llm("cancelled", status="cancelled"),
            {"node_id": "tool:error", "kind": "error", "status": "failed", "tool_name": "read_file", "duration_ms": 90000},
        ])
        self.assertEqual(metrics.total_tokens, 125)
        self.assertTrue(metrics.total_partial)
        self.assertEqual(metrics.average_duration_ms, 10000)

    def test_explicit_total_takes_priority_and_invalid_duration_is_excluded(self) -> None:
        metrics = metrics_from_nodes([
            _llm("a", usage={"input_tokens": 8, "output_tokens": 2, "total_tokens": 12}, duration_ms=-1),
            _llm("b", duration_ms=float("nan")),
            _llm("c", duration_ms=float("inf")),
        ])
        self.assertEqual(metrics.total_tokens, 12)
        self.assertTrue(metrics.total_partial)
        self.assertIsNone(metrics.average_duration_ms)

    def test_accumulator_ignores_non_llm_events_and_duplicate_delivery(self) -> None:
        accumulator = TraceMetricsAccumulator()
        event = {"event_id": "end-1", "type": "llm_end", "status": "completed", "duration_ms": 30000,
                 "usage": {"input_tokens": 12000, "output_tokens": 3000}}
        accumulator.add_event({"event_id": "start", "type": "llm_start", "status": "running"})
        self.assertIsNone(accumulator.snapshot().total_tokens)
        accumulator.add_event(event)
        accumulator.add_event(event)
        accumulator.add_event({"event_id": "tool", "type": "tool_end", "status": "completed", "duration_ms": 90000,
                               "usage": {"total_tokens": 99999}})
        accumulator.add_event({"event_id": "failed", "type": "llm_error", "status": "failed", "duration_ms": 60000})
        metrics = accumulator.snapshot()
        self.assertEqual(metrics.total_tokens, 15000)
        self.assertEqual(metrics.average_duration_ms, 30000)
        self.assertTrue(metrics.total_partial)

    def test_accumulator_replaces_replayed_sample_without_adding_another_call(self) -> None:
        accumulator = TraceMetricsAccumulator()
        accumulator.add_event({"event_id": "same", "type": "llm_end", "status": "completed", "duration_ms": 10000})
        accumulator.add_event({"event_id": "same", "type": "llm_end", "status": "completed", "duration_ms": 30000,
                               "usage": {"input_tokens": 8000, "output_tokens": 2000}})
        metrics = accumulator.snapshot()
        self.assertEqual(metrics.total_tokens, 10000)
        self.assertFalse(metrics.total_partial)
        self.assertEqual(metrics.average_duration_ms, 30000)

    def test_accumulator_replays_legacy_inline_output_usage(self) -> None:
        for field in ("usage", "usage_metadata"):
            with self.subTest(field=field):
                accumulator = TraceMetricsAccumulator()
                accumulator.add_event({
                    "event_id": "legacy", "type": "llm_end", "status": "completed", "duration_ms": 10000,
                    "usage": None, "output": {"messages": [{field: {"input_tokens": 12000, "output_tokens": 3000}}]},
                })
                metrics = accumulator.snapshot()
                self.assertEqual((metrics.input_tokens, metrics.output_tokens, metrics.total_tokens), (12000, 3000, 15000))
                self.assertEqual(metrics.average_duration_ms, 10000)
                self.assertFalse(metrics.total_partial)

    def test_inline_usage_fallback_preserves_precedence_and_missing_values(self) -> None:
        self.assertIsNone(usage_from_output(None))
        self.assertIsNone(usage_from_output({"payload_id": "external-output"}))
        self.assertEqual(usage_from_output({"messages": [
            {"usage": {"input_tokens": 5, "output_tokens": 4}},
            {"usage_metadata": {"prompt_tokens": 0, "completion_tokens": 2}},
        ]}), {"input_tokens": 0, "output_tokens": 2, "total_tokens": 2})
        self.assertIsNone(usage_from_output({"messages": [{"usage": {"output_tokens": 3000}}]})["total_tokens"])
        accumulator = TraceMetricsAccumulator()
        accumulator.add_event({
            "event_id": "explicit", "type": "llm_end", "status": "completed",
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "output": {"messages": [{"usage_metadata": {"input_tokens": 12000, "output_tokens": 3000}}]},
        })
        self.assertEqual(accumulator.snapshot().total_tokens, 0)

    def test_terminal_snapshot_marks_only_unpaired_distinct_model_starts_missing(self) -> None:
        accumulator = TraceMetricsAccumulator()
        start = {"event_id": "start-1", "type": "llm_start", "status": "running", "run_id": "run-1", "agent_name": "writer"}
        end = {"event_id": "end-1", "type": "llm_end", "status": "completed", "run_id": "run-1", "agent_name": "writer",
               "duration_ms": 10000, "usage": {"input_tokens": 12000, "output_tokens": 3000}}
        for event in [start, start, end, end, start]:
            accumulator.add_event(event)
        self.assertFalse(accumulator.snapshot(terminal=True).total_partial)
        pending = {"event_id": "start-2", "type": "llm_start", "status": "running", "run_id": "run-1", "agent_name": "subagent"}
        accumulator.add_event(pending)
        accumulator.add_event(pending)
        self.assertFalse(accumulator.snapshot().total_partial)
        final = accumulator.snapshot(terminal=True)
        self.assertEqual(final.total_tokens, 15000)
        self.assertTrue(final.total_partial)
        self.assertEqual(final.average_duration_ms, 10000)
        accumulator.add_event({"event_id": "end-2", "type": "llm_error", "status": "failed", "run_id": "run-1", "agent_name": "subagent",
                               "usage": {"input_tokens": 0, "output_tokens": 0}})
        self.assertFalse(accumulator.snapshot(terminal=True).total_partial)


if __name__ == "__main__":
    unittest.main()
