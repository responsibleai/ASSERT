# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Compatibility tests for the optional Phoenix span collector."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

PHOENIX_AVAILABLE = importlib.util.find_spec("phoenix") is not None


def _raw_span(
    span_id: str,
    *,
    trace_id: str = "trace-1",
    parent_id: str | None = None,
    start_time: str = "2026-09-01T00:00:00Z",
    end_time: str = "2026-09-01T00:00:01Z",
    attributes: dict | None = None,
    events: list[dict] | None = None,
) -> dict:
    return {
        "id": f"relay-{span_id}",
        "name": "model call",
        "context": {"trace_id": trace_id, "span_id": span_id},
        "parent_id": parent_id,
        "span_kind": "LLM",
        "start_time": start_time,
        "end_time": end_time,
        "status_code": "OK",
        "status_message": "",
        "attributes": attributes or {"openinference.span.kind": "LLM"},
        "events": events or [],
    }


class PhoenixCollectorMissingDependencyTest(unittest.TestCase):
    def test_missing_dependency_has_actionable_install_error(self) -> None:
        from assert_ai.core.collector import PhoenixCollector

        with patch.dict(sys.modules, {"phoenix": None, "phoenix.client": None}):
            with self.assertRaisesRegex(ImportError, r"assert-ai\[phoenix\]"):
                PhoenixCollector()


@unittest.skipUnless(PHOENIX_AVAILABLE, "install assert-ai[phoenix] to test the adapter")
class PhoenixCollectorCompatibilityTest(unittest.TestCase):
    """Exercise the real Phoenix client surface installed by the public extra."""

    def test_constructs_with_supported_phoenix_client(self) -> None:
        from assert_ai.core.collector import PhoenixCollector

        collector = PhoenixCollector(
            endpoint="http://localhost:6006",
            project_name="review-project",
        )

        self.assertTrue(hasattr(collector._client, "spans"))
        self.assertTrue(hasattr(collector._client.spans, "get_spans_dataframe"))

    def test_raw_query_preserves_events_and_pushes_trace_filter_to_phoenix(self) -> None:
        from assert_ai.core.collector import PhoenixCollector
        from assert_ai.core.otel import _spans_to_events

        message = json.dumps({
            "role": "assistant",
            "content": "event answer",
            "tool_calls": [{
                "id": "call-1",
                "type": "function",
                "function": {"name": "lookup", "arguments": '{"q":"x"}'},
            }],
        })
        raw_span = _raw_span(
            "span-event",
            start_time="2026-09-01T00:00:00.000001Z",
            attributes={"gen_ai.operation.name": "chat"},
            events=[{
                "name": "gen_ai.choice",
                "timestamp": "2026-09-01T00:00:00.500000Z",
                "attributes": {"message": message},
            }, {
                "name": "gen_ai.tool.message",
                "timestamp": "2026-09-01T00:00:00.750000Z",
                "attributes": {"id": "call-1", "content": '{"answer":42}'},
            }],
        )
        collector = PhoenixCollector(project_name="review-project")

        with patch.object(
            collector._client.spans,
            "get_spans",
            return_value=[raw_span],
        ) as get_spans:
            spans = collector.get_spans(trace_ids=["trace-1"])

        query = get_spans.call_args.kwargs
        self.assertEqual(query["project_identifier"], "review-project")
        self.assertEqual(query["trace_ids"], ["trace-1"])
        self.assertEqual(query["limit"], sys.maxsize)
        self.assertEqual(spans[0].start_time_ns, 1_788_220_800_000_001_000)
        self.assertEqual(len(spans[0].events), 2)
        events, aggregate = _spans_to_events(spans)
        self.assertEqual(events[0]["edit"]["message"]["content"], "event answer")
        self.assertEqual(events[1]["edit"]["tool_name"], "lookup")
        self.assertEqual(events[1]["edit"]["tool_args"], {"q": "x"})
        self.assertEqual(
            json.loads(events[1]["edit"]["tool_result"]),
            {"answer": 42},
        )
        self.assertEqual(aggregate["llm_call_count"], 1)

    def test_raw_query_follows_phoenix_cursor_pages(self) -> None:
        from assert_ai.core.collector import PhoenixCollector

        class Response:
            def __init__(self, payload: dict):
                self._payload = payload

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return self._payload

        collector = PhoenixCollector(project_name="review-project")
        responses = [
            Response({"data": [_raw_span("span-page-1")], "next_cursor": "page-2"}),
            Response({"data": [_raw_span("span-page-2")]}),
        ]

        with patch.object(
            collector._client.spans._client,
            "get",
            side_effect=responses,
        ) as request:
            spans = collector.get_spans()

        self.assertEqual([span.span_id for span in spans], ["span-page-1", "span-page-2"])
        self.assertEqual(request.call_count, 2)
        self.assertNotIn("cursor", request.call_args_list[0].kwargs["params"])
        self.assertEqual(request.call_args_list[1].kwargs["params"]["cursor"], "page-2")

    def test_legacy_dataframe_fallback_uses_explicit_limit_and_converts_spans(self) -> None:
        import pandas as pd  # type: ignore[import-not-found]

        from assert_ai.core.collector import PhoenixCollector

        start = pd.Timestamp("2026-09-01T00:00:00Z")
        end = pd.Timestamp("2026-09-01T00:00:01Z")
        dataframe = pd.DataFrame(
            {
                "context.trace_id": ["trace-1", "trace-2"],
                "parent_id": [None, "span-parent"],
                "name": ["model call", "tool call"],
                "span_kind": ["LLM", "TOOL"],
                "start_time": [start, start],
                "end_time": [end, end],
                "attributes.session.id": ["session-1", "session-1"],
                "attributes.output.value": ["answer", "tool result"],
                "attributes.llm.model_name": ["gpt-5.4", None],
            },
            index=pd.Index(["span-1", "span-2"], name="context.span_id"),
        )
        collector = PhoenixCollector(
            endpoint="http://localhost:6006",
            project_name="review-project",
        )

        with (
            patch.object(collector._client.spans, "get_spans", None),
            patch.object(
                collector._client.spans,
                "get_spans_dataframe",
                return_value=dataframe,
            ) as get_spans,
        ):
            spans = collector.get_spans(
                start_time="2026-09-01T00:00:00Z",
                end_time="2026-09-01T00:00:01+00:00",
                trace_ids=["trace-1"],
            )

        query = get_spans.call_args.kwargs
        self.assertEqual(query["project_identifier"], "review-project")
        self.assertIsInstance(query["start_time"], datetime)
        self.assertIsInstance(query["end_time"], datetime)
        self.assertEqual(query["start_time"].tzinfo, UTC)
        self.assertEqual(query["end_time"].tzinfo, UTC)
        self.assertGreater(query["limit"], 1000)

        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.trace_id, "trace-1")
        self.assertEqual(span.span_id, "span-1")
        self.assertIsNone(span.parent_span_id)
        self.assertEqual(span.kind, "LLM")
        self.assertEqual(span.start_time_ns, start.value)
        self.assertEqual(span.end_time_ns, end.value)
        self.assertEqual(span.attributes["session.id"], "session-1")
        self.assertEqual(span.attributes["output.value"], "answer")

    def test_orders_newest_first_phoenix_results_chronologically(self) -> None:
        from assert_ai.core.collector import PhoenixCollector

        initial = _raw_span(
            "span-initial",
            start_time="2026-09-01T00:00:00Z",
            end_time="2026-09-01T00:00:01Z",
        )
        final = _raw_span(
            "span-final",
            start_time="2026-09-01T00:00:02Z",
            end_time="2026-09-01T00:00:03Z",
        )
        collector = PhoenixCollector(project_name="review-project")

        with patch.object(
            collector._client.spans,
            "get_spans",
            return_value=[final, initial],
        ):
            spans = collector.get_spans()

        self.assertEqual([span.span_id for span in spans], ["span-initial", "span-final"])

    def test_orders_parent_before_child_when_timestamps_tie(self) -> None:
        from assert_ai.core.collector import PhoenixCollector

        parent = _raw_span("span-parent")
        child = _raw_span("span-child", parent_id="span-parent")
        collector = PhoenixCollector(project_name="review-project")

        with patch.object(
            collector._client.spans,
            "get_spans",
            return_value=[child, parent],
        ):
            spans = collector.get_spans()

        self.assertEqual([span.span_id for span in spans], ["span-parent", "span-child"])

    def test_orders_deep_parent_chain_without_recursion(self) -> None:
        from assert_ai.core.collector import PhoenixCollector

        raw_spans = [
            _raw_span(
                f"span-{index:04d}",
                parent_id=(f"span-{index - 1:04d}" if index else None),
            )
            for index in range(1100)
        ]
        collector = PhoenixCollector(project_name="review-project")

        with patch.object(
            collector._client.spans,
            "get_spans",
            return_value=list(reversed(raw_spans)),
        ):
            spans = collector.get_spans()

        self.assertEqual(
            [span.span_id for span in spans],
            [f"span-{index:04d}" for index in range(1100)],
        )

    def test_orders_parent_cycle_independently_of_phoenix_input_order(self) -> None:
        from assert_ai.core.collector import PhoenixCollector

        span_a = _raw_span("span-a", parent_id="span-b")
        span_b = _raw_span("span-b", parent_id="span-a")
        collector = PhoenixCollector(project_name="review-project")
        orders = []
        for raw_order in ([span_a, span_b], [span_b, span_a]):
            with patch.object(
                collector._client.spans,
                "get_spans",
                return_value=raw_order,
            ):
                orders.append([span.span_id for span in collector.get_spans()])

        self.assertEqual(orders, [["span-a", "span-b"], ["span-a", "span-b"]])

    def test_rejects_invalid_or_timezone_naive_bounds_before_query(self) -> None:
        from assert_ai.core.collector import PhoenixCollector

        collector = PhoenixCollector(project_name="review-project")
        with patch.object(collector._client.spans, "get_spans") as get_spans:
            with self.assertRaisesRegex(ValueError, "valid ISO-8601"):
                collector.get_spans(start_time="not-a-timestamp")
            with self.assertRaisesRegex(ValueError, "timezone offset"):
                collector.get_spans(start_time="2026-09-01T00:00:00")

        get_spans.assert_not_called()

    def test_otel_session_consumes_bounded_phoenix_spans(self) -> None:
        from assert_ai.core.collector import PhoenixCollector
        from assert_ai.core.model_client import Message
        from assert_ai.core.otel_session import OTelTracedSession

        raw_span = _raw_span(
            "span-session",
            trace_id="trace-session",
            attributes={
                "openinference.span.kind": "LLM",
                "session.id": "session-1",
                "output.value": "answer",
                "llm.model_name": "gpt-5.4",
                "llm.token_count.prompt": 5,
                "llm.token_count.completion": 2,
            },
        )
        collector = PhoenixCollector(project_name="review-project")
        target_module = types.ModuleType("_phoenix_collector_target")
        setattr(target_module, "target", lambda message: f"response to {message}")
        sys.modules[target_module.__name__] = target_module

        async def run_session():
            session = OTelTracedSession(
                callable_ref="_phoenix_collector_target:target",
                collector=collector,
            )
            await session.open()
            try:
                return await session.run_turn([Message(role="user", content="test")])
            finally:
                await session.close()

        try:
            with patch.object(
                collector._client.spans,
                "get_spans",
                return_value=[raw_span],
            ) as get_spans:
                result = asyncio.run(run_session())
        finally:
            sys.modules.pop(target_module.__name__, None)

        query = get_spans.call_args.kwargs
        self.assertIsInstance(query["start_time"], datetime)
        self.assertIsInstance(query["end_time"], datetime)
        self.assertLessEqual(query["start_time"], query["end_time"])
        assert result.raw is not None
        self.assertTrue(result.raw["span_validation"]["valid"])
        self.assertEqual(len(result.raw["trace_events"]), 1)
        self.assertEqual(result.raw["trace_metadata"]["llm_call_count"], 1)


if __name__ == "__main__":
    unittest.main()
