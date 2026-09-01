# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Compatibility tests for the optional Phoenix span collector."""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

PHOENIX_AVAILABLE = importlib.util.find_spec("phoenix") is not None


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

    def test_queries_with_datetime_bounds_and_converts_phoenix_dataframe(self) -> None:
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

        with patch.object(
            collector._client.spans,
            "get_spans_dataframe",
            return_value=dataframe,
        ) as get_spans:
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

    def test_rejects_invalid_or_timezone_naive_bounds_before_query(self) -> None:
        from assert_ai.core.collector import PhoenixCollector

        collector = PhoenixCollector(project_name="review-project")
        with patch.object(collector._client.spans, "get_spans_dataframe") as get_spans:
            with self.assertRaisesRegex(ValueError, "valid ISO-8601"):
                collector.get_spans(start_time="not-a-timestamp")
            with self.assertRaisesRegex(ValueError, "timezone offset"):
                collector.get_spans(start_time="2026-09-01T00:00:00")

        get_spans.assert_not_called()

    def test_otel_session_consumes_bounded_phoenix_spans(self) -> None:
        import pandas as pd  # type: ignore[import-not-found]

        from assert_ai.core.collector import PhoenixCollector
        from assert_ai.core.model_client import Message
        from assert_ai.core.otel_session import OTelTracedSession

        dataframe = pd.DataFrame(
            {
                "context.trace_id": ["trace-session"],
                "parent_id": [None],
                "name": ["model call"],
                "span_kind": ["LLM"],
                "start_time": [pd.Timestamp("2026-09-01T00:00:00Z")],
                "end_time": [pd.Timestamp("2026-09-01T00:00:01Z")],
                "attributes.session.id": ["session-1"],
                "attributes.output.value": ["answer"],
                "attributes.llm.model_name": ["gpt-5.4"],
                "attributes.llm.token_count.prompt": [5],
                "attributes.llm.token_count.completion": [2],
            },
            index=pd.Index(["span-session"], name="context.span_id"),
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
                "get_spans_dataframe",
                return_value=dataframe,
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
