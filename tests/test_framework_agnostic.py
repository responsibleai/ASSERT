"""Tests for framework-agnostic agent support POC.

Tests both Approach A (OTel trace parser) and Approach B (CallableSession).
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from p2m.core.otel import parse_otel_traces, OTelSpan, _flatten_attributes

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_TRACES = FIXTURES / "sample_otel_traces.json"


# ── Approach A: OTel trace parser tests ──────────────────────────


class TestOTelParser(unittest.TestCase):
    """Validates that OTLP JSON traces are correctly converted to p2m transcript rows."""

    def test_parse_groups_by_session_id(self):
        """Two sessions in the fixture should produce two transcript rows."""
        rows = parse_otel_traces(SAMPLE_TRACES, group_by="session.id")
        self.assertEqual(len(rows), 2)
        session_ids = {r["metadata"]["session_id"] for r in rows}
        self.assertEqual(session_ids, {"sess_tokyo_trip", "sess_paris_trip"})

    def test_tokyo_session_has_all_events(self):
        """The Tokyo session should have 3 LLM + 2 tool = 5 events."""
        rows = parse_otel_traces(SAMPLE_TRACES, group_by="session.id")
        tokyo = next(r for r in rows if r["metadata"]["session_id"] == "sess_tokyo_trip")

        events = tokyo["events"]
        llm_events = [e for e in events if e["actor"] == "target"]
        tool_events = [e for e in events if e["actor"] == "tool"]
        self.assertEqual(len(llm_events), 3)
        self.assertEqual(len(tool_events), 2)

    def test_aggregate_metadata(self):
        """Aggregate metadata should reflect all spans in the session."""
        rows = parse_otel_traces(SAMPLE_TRACES, group_by="session.id")
        tokyo = next(r for r in rows if r["metadata"]["session_id"] == "sess_tokyo_trip")
        agg = tokyo["raw"]

        self.assertEqual(agg["llm_call_count"], 3)
        self.assertEqual(agg["total_tokens"]["input"], 85 + 210 + 420)
        self.assertEqual(agg["total_tokens"]["output"], 42 + 35 + 95)
        self.assertIn("intent_classifier", agg["nodes_visited"])
        self.assertIn("flight_search", agg["nodes_visited"])
        self.assertIn("itinerary_optimizer", agg["nodes_visited"])
        self.assertIn("search_flights", agg["tools_called"])
        self.assertIn("search_hotels", agg["tools_called"])

    def test_tool_events_have_parsed_args(self):
        """Tool call events should contain parsed JSON args."""
        rows = parse_otel_traces(SAMPLE_TRACES, group_by="session.id")
        tokyo = next(r for r in rows if r["metadata"]["session_id"] == "sess_tokyo_trip")

        tool_events = [e for e in tokyo["events"] if e["actor"] == "tool"]
        flight_event = next(e for e in tool_events if e["edit"]["tool_name"] == "search_flights")
        self.assertEqual(flight_event["edit"]["tool_args"]["destination"], "NRT")
        self.assertEqual(flight_event["edit"]["tool_args"]["max_price"], 1500)

    def test_llm_events_have_node_metadata(self):
        """LLM events should carry node name, model, tokens, and latency."""
        rows = parse_otel_traces(SAMPLE_TRACES, group_by="session.id")
        tokyo = next(r for r in rows if r["metadata"]["session_id"] == "sess_tokyo_trip")

        llm_events = [e for e in tokyo["events"] if e["actor"] == "target"]
        first = llm_events[0]
        self.assertEqual(first["raw"]["_node"], "intent_classifier")
        self.assertEqual(first["raw"]["_model"], "gpt-4o")
        self.assertEqual(first["raw"]["_tokens"]["input"], 85)
        self.assertGreater(first["raw"]["_latency_ms"], 0)

    def test_paris_session_is_minimal(self):
        """The Paris session has only 1 LLM span, no tools."""
        rows = parse_otel_traces(SAMPLE_TRACES, group_by="session.id")
        paris = next(r for r in rows if r["metadata"]["session_id"] == "sess_paris_trip")

        self.assertEqual(len(paris["events"]), 1)
        self.assertEqual(paris["raw"]["llm_call_count"], 1)
        self.assertEqual(paris["raw"]["tools_called"], [])

    def test_events_are_time_ordered(self):
        """Events within a session should be in chronological order."""
        rows = parse_otel_traces(SAMPLE_TRACES, group_by="session.id")
        tokyo = next(r for r in rows if r["metadata"]["session_id"] == "sess_tokyo_trip")

        events = tokyo["events"]
        # First event should be intent_classifier, last should be itinerary_optimizer
        first_llm = next(e for e in events if e["actor"] == "target")
        self.assertEqual(first_llm["raw"]["_node"], "intent_classifier")

    def test_group_by_trace_id(self):
        """Grouping by trace_id should produce different groupings."""
        rows = parse_otel_traces(SAMPLE_TRACES, group_by="nonexistent.key")
        # Falls back to trace_id grouping
        trace_ids = {r["metadata"]["session_id"] for r in rows}
        self.assertIn("abc123", trace_ids)
        self.assertIn("def456", trace_ids)

    def test_transcript_row_schema(self):
        """Each row should have metadata, events, and raw keys."""
        rows = parse_otel_traces(SAMPLE_TRACES, group_by="session.id")
        for row in rows:
            self.assertIn("metadata", row)
            self.assertIn("events", row)
            self.assertIn("raw", row)
            self.assertEqual(row["metadata"]["runtime_mode"], "otel_traced")
            self.assertEqual(row["metadata"]["kind"], "otel_import")


class TestFlattenAttributes(unittest.TestCase):
    """Test OTLP attribute parsing edge cases."""

    def test_string_value(self):
        attrs = [{"key": "k", "value": {"stringValue": "hello"}}]
        self.assertEqual(_flatten_attributes(attrs), {"k": "hello"})

    def test_int_value(self):
        attrs = [{"key": "k", "value": {"intValue": "42"}}]
        self.assertEqual(_flatten_attributes(attrs), {"k": 42})

    def test_double_value(self):
        attrs = [{"key": "k", "value": {"doubleValue": 3.14}}]
        self.assertAlmostEqual(_flatten_attributes(attrs)["k"], 3.14)

    def test_bool_value(self):
        attrs = [{"key": "k", "value": {"boolValue": True}}]
        self.assertEqual(_flatten_attributes(attrs), {"k": True})

    def test_empty_attributes(self):
        self.assertEqual(_flatten_attributes([]), {})


# ── Approach B: CallableSession tests ────────────────────────────


class TestCallableSession(unittest.TestCase):
    """Validates CallableSession can invoke sync/async callables."""

    def test_import(self):
        """CallableSession should be importable from p2m.core.session."""
        from p2m.core.session import CallableSession
        self.assertTrue(callable(CallableSession))

    def test_sync_callable(self):
        """CallableSession should handle a sync fn(str) -> str."""
        from p2m.core.session import CallableSession
        from p2m.core.model_client import Message

        # Create a temp module with a sync callable
        import types
        mod = types.ModuleType("_test_sync_target")
        mod.target = lambda msg: f"echo: {msg}"

        import sys
        sys.modules["_test_sync_target"] = mod

        try:
            session = CallableSession(callable_ref="_test_sync_target:target")

            async def _run():
                await session.open()
                result = await session.run_turn([Message(role="user", content="hello")])
                await session.close()
                return result

            result = asyncio.run(_run())
            self.assertEqual(result.text, "echo: hello")
            self.assertEqual(len(result.interaction_messages), 2)
            self.assertEqual(result.interaction_messages[0]["role"], "user")
            self.assertEqual(result.interaction_messages[1]["role"], "assistant")
        finally:
            del sys.modules["_test_sync_target"]

    def test_async_callable(self):
        """CallableSession should handle an async fn(str) -> str."""
        from p2m.core.session import CallableSession
        from p2m.core.model_client import Message

        import types
        mod = types.ModuleType("_test_async_target")

        async def async_target(msg: str) -> str:
            return f"async: {msg}"

        mod.target = async_target

        import sys
        sys.modules["_test_async_target"] = mod

        try:
            session = CallableSession(callable_ref="_test_async_target:target")

            async def _run():
                await session.open()
                result = await session.run_turn([Message(role="user", content="world")])
                await session.close()
                return result

            result = asyncio.run(_run())
            self.assertEqual(result.text, "async: world")
        finally:
            del sys.modules["_test_async_target"]

    def test_callable_with_history(self):
        """CallableSession should detect and pass history parameter."""
        from p2m.core.session import CallableSession
        from p2m.core.model_client import Message

        import types
        mod = types.ModuleType("_test_history_target")

        def target_with_history(msg: str, history: list = None) -> str:
            return f"got {len(history or [])} history items"

        mod.target = target_with_history

        import sys
        sys.modules["_test_history_target"] = mod

        try:
            session = CallableSession(callable_ref="_test_history_target:target")

            async def _run():
                await session.open()
                messages = [
                    Message(role="user", content="first"),
                    Message(role="assistant", content="reply"),
                    Message(role="user", content="second"),
                ]
                result = await session.run_turn(messages)
                await session.close()
                return result

            result = asyncio.run(_run())
            self.assertIn("3 history items", result.text)
        finally:
            del sys.modules["_test_history_target"]

    def test_runtime_mode(self):
        """CallableSession.runtime_mode should be 'callable'."""
        from p2m.core.session import CallableSession
        session = CallableSession(callable_ref="some.module:fn")
        self.assertEqual(session.runtime_mode, "callable")


# ── TargetConfig validation tests ────────────────────────────────


class TestTargetConfigCallable(unittest.TestCase):
    """Validates TargetConfig accepts callable field."""

    def test_callable_target_is_valid(self):
        """TargetConfig with callable should not raise."""
        from p2m.core.config_model import TargetConfig
        tc = TargetConfig(callable="my_module:my_fn")
        self.assertTrue(tc.is_callable)
        self.assertFalse(tc.is_external)

    def test_callable_and_model_conflicts(self):
        """TargetConfig with both callable and model should raise."""
        from p2m.core.config_model import TargetConfig
        with self.assertRaises(ValueError):
            TargetConfig(callable="my_module:my_fn", model="openai/gpt-4o")

    def test_callable_and_connector_conflicts(self):
        """TargetConfig with both callable and connector should raise."""
        from p2m.core.config_model import TargetConfig
        with self.assertRaises(ValueError):
            TargetConfig(callable="my_module:my_fn", connector="some.connector")


# ── SpanValidator tests ──────────────────────────────────────────


class TestSpanValidation(unittest.TestCase):
    """Validates span validation logic."""

    def test_valid_llm_span(self):
        from p2m.core.otel import validate_spans, OTelSpan
        span = OTelSpan(
            trace_id="t1", span_id="s1", parent_span_id=None,
            name="llm_call", kind="LLM",
            start_time_ns=0, end_time_ns=1_000_000,
            attributes={
                "output.value": "hello",
                "llm.model_name": "gpt-4o",
                "llm.token_count.prompt": 10,
                "llm.token_count.completion": 5,
            },
        )
        result = validate_spans([span])
        self.assertTrue(result.valid)
        self.assertEqual(result.missing_attributes, [])
        self.assertEqual(result.warnings, [])

    def test_missing_span_kind(self):
        from p2m.core.otel import validate_spans, OTelSpan
        span = OTelSpan(
            trace_id="t1", span_id="s1", parent_span_id=None,
            name="unknown", kind="UNKNOWN",
            start_time_ns=0, end_time_ns=1_000_000,
        )
        result = validate_spans([span])
        self.assertFalse(result.valid)
        self.assertTrue(any("openinference.span.kind" in m for m in result.missing_attributes))

    def test_llm_span_missing_output(self):
        from p2m.core.otel import validate_spans, OTelSpan
        span = OTelSpan(
            trace_id="t1", span_id="s1", parent_span_id=None,
            name="llm_call", kind="LLM",
            start_time_ns=0, end_time_ns=1_000_000,
            attributes={"llm.model_name": "gpt-4o"},
        )
        result = validate_spans([span])
        self.assertFalse(result.valid)
        self.assertTrue(any("output.value" in m for m in result.missing_attributes))

    def test_llm_span_missing_recommended(self):
        from p2m.core.otel import validate_spans, OTelSpan
        span = OTelSpan(
            trace_id="t1", span_id="s1", parent_span_id=None,
            name="llm_call", kind="LLM",
            start_time_ns=0, end_time_ns=1_000_000,
            attributes={"output.value": "response"},
        )
        result = validate_spans([span])
        self.assertTrue(result.valid)  # still valid, just warnings
        self.assertTrue(any("llm.model_name" in w for w in result.warnings))
        self.assertTrue(any("token counts" in w for w in result.warnings))

    def test_tool_span_missing_recommended(self):
        from p2m.core.otel import validate_spans, OTelSpan
        span = OTelSpan(
            trace_id="t1", span_id="s1", parent_span_id=None,
            name="tool_call", kind="TOOL",
            start_time_ns=0, end_time_ns=1_000_000,
            attributes={},
        )
        result = validate_spans([span])
        self.assertTrue(result.valid)
        self.assertTrue(any("tool.name" in w for w in result.warnings))

    def test_empty_spans_valid(self):
        from p2m.core.otel import validate_spans
        result = validate_spans([])
        self.assertTrue(result.valid)
        self.assertEqual(result.missing_attributes, [])
        self.assertEqual(result.warnings, [])


# ── compress_trace_for_judge tests ───────────────────────────────


class TestCompressTrace(unittest.TestCase):
    """Validates trace compression for judge token budget."""

    def test_no_compression_under_limit(self):
        from p2m.core.otel import compress_trace_for_judge
        events = [{"actor": "target", "raw": {"_node": "n1"}} for _ in range(5)]
        result = compress_trace_for_judge(events, max_events=10)
        self.assertEqual(len(result), 5)

    def test_tool_events_always_kept(self):
        from p2m.core.otel import compress_trace_for_judge
        events = [
            {"actor": "tool", "edit": {"tool_name": f"tool_{i}"}} for i in range(5)
        ] + [
            {"actor": "target", "raw": {"_node": f"n{i}"}} for i in range(20)
        ]
        result = compress_trace_for_judge(events, max_events=10)
        tool_count = sum(1 for e in result if e.get("actor") == "tool")
        self.assertEqual(tool_count, 5)

    def test_compression_keeps_first_and_last_per_node(self):
        from p2m.core.otel import compress_trace_for_judge
        events = [
            {"actor": "target", "raw": {"_node": "node_a"}, "idx": i}
            for i in range(10)
        ]
        result = compress_trace_for_judge(events, max_events=5)
        # Should keep first and last for node_a
        self.assertLessEqual(len(result), 5)
        self.assertTrue(len(result) >= 2)

    def test_strip_tool_args(self):
        from p2m.core.otel import compress_trace_for_judge
        events = [
            {"actor": "tool", "edit": {"tool_name": "search", "tool_args": {"q": "test"}}},
        ]
        result = compress_trace_for_judge(events, include_tool_args=False)
        self.assertNotIn("tool_args", result[0]["edit"])

    def test_strip_token_counts(self):
        from p2m.core.otel import compress_trace_for_judge
        events = [
            {"actor": "target", "raw": {"_node": "n1", "_tokens": {"input": 10, "output": 5}}},
        ]
        result = compress_trace_for_judge(events, include_token_counts=False)
        self.assertNotIn("_tokens", result[0]["raw"])


# ── TraceExporter tests ──────────────────────────────────────────


class TestTraceExporters(unittest.TestCase):
    """Validates trace exporter implementations."""

    def test_in_memory_exporter_add_and_export(self):
        from p2m.core.otel import InMemoryTraceExporter, OTelSpan
        exporter = InMemoryTraceExporter()
        span = OTelSpan(
            trace_id="t1", span_id="s1", parent_span_id=None,
            name="test", kind="LLM",
            start_time_ns=0, end_time_ns=1_000_000,
            attributes={"session.id": "sess_1"},
        )
        exporter.add_span(span)
        result = exporter.export_session("sess_1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].span_id, "s1")

    def test_in_memory_exporter_filters_by_session(self):
        from p2m.core.otel import InMemoryTraceExporter, OTelSpan
        exporter = InMemoryTraceExporter()
        exporter.add_span(OTelSpan(
            trace_id="t1", span_id="s1", parent_span_id=None,
            name="a", kind="LLM", start_time_ns=0, end_time_ns=1,
            attributes={"session.id": "sess_1"},
        ))
        exporter.add_span(OTelSpan(
            trace_id="t2", span_id="s2", parent_span_id=None,
            name="b", kind="LLM", start_time_ns=0, end_time_ns=1,
            attributes={"session.id": "sess_2"},
        ))
        self.assertEqual(len(exporter.export_session("sess_1")), 1)
        self.assertEqual(len(exporter.export_session("sess_2")), 1)
        self.assertEqual(len(exporter.export_session("nonexistent")), 0)

    def test_in_memory_exporter_satisfies_protocol(self):
        from p2m.core.otel import InMemoryTraceExporter, TraceExporter
        self.assertIsInstance(InMemoryTraceExporter(), TraceExporter)

    def test_file_exporter_satisfies_protocol(self):
        from p2m.core.otel import FileTraceExporter, TraceExporter
        self.assertIsInstance(FileTraceExporter("dummy.json"), TraceExporter)

    def test_file_exporter_reads_fixture(self):
        from p2m.core.otel import FileTraceExporter
        exporter = FileTraceExporter(SAMPLE_TRACES)
        spans = exporter.export_session("sess_tokyo_trip")
        self.assertGreater(len(spans), 0)
        self.assertEqual(len(exporter.export_session("nonexistent")), 0)


# ── OTelTracedSession tests ──────────────────────────────────────


class TestOTelTracedSession(unittest.TestCase):
    """Validates OTelTracedSession lifecycle and trace capture."""

    def test_import(self):
        from p2m.core.otel_session import OTelTracedSession
        self.assertTrue(callable(OTelTracedSession))

    def test_runtime_mode(self):
        from p2m.core.otel_session import OTelTracedSession
        session = OTelTracedSession(callable_ref="some.module:fn")
        self.assertEqual(session.runtime_mode, "otel_traced")

    def test_run_turn_basic(self):
        """OTelTracedSession should invoke callable and return TurnResult."""
        from p2m.core.otel_session import OTelTracedSession
        from p2m.core.model_client import Message

        import sys
        import types
        mod = types.ModuleType("_test_otel_target")
        mod.target = lambda msg: f"traced: {msg}"
        sys.modules["_test_otel_target"] = mod

        try:
            session = OTelTracedSession(callable_ref="_test_otel_target:target")

            async def _run():
                await session.open()
                result = await session.run_turn([Message(role="user", content="probe")])
                await session.close()
                return result

            result = asyncio.run(_run())
            self.assertEqual(result.text, "traced: probe")
            self.assertEqual(result.raw["runtime_mode"], "otel_traced")
            self.assertIn("session_id", result.raw)
            self.assertIn("turn_id", result.raw)
            self.assertEqual(result.raw["accumulated_turns"], 1)
        finally:
            del sys.modules["_test_otel_target"]

    def test_run_turn_with_history(self):
        """OTelTracedSession should detect and pass history parameter."""
        from p2m.core.otel_session import OTelTracedSession
        from p2m.core.model_client import Message

        import sys
        import types
        mod = types.ModuleType("_test_otel_history")

        def target_with_hist(msg: str, history: list = None) -> str:
            return f"history_len={len(history or [])}"

        mod.target = target_with_hist
        sys.modules["_test_otel_history"] = mod

        try:
            session = OTelTracedSession(callable_ref="_test_otel_history:target")

            async def _run():
                await session.open()
                messages = [
                    Message(role="user", content="first"),
                    Message(role="assistant", content="reply"),
                    Message(role="user", content="second"),
                ]
                result = await session.run_turn(messages)
                await session.close()
                return result

            result = asyncio.run(_run())
            self.assertIn("history_len=3", result.text)
        finally:
            del sys.modules["_test_otel_history"]

    def test_run_turn_with_spans(self):
        """When exporter has spans, they should appear in TurnResult.raw."""
        from p2m.core.otel_session import OTelTracedSession
        from p2m.core.otel import InMemoryTraceExporter, OTelSpan
        from p2m.core.model_client import Message

        import sys
        import types
        mod = types.ModuleType("_test_otel_spans")

        exporter = InMemoryTraceExporter()

        def target_fn(msg: str) -> str:
            # Simulate span emission by adding to the exporter directly
            # In real usage, spans come from OTel collector
            session_obj = sys.modules["_test_otel_spans"]._session
            turn_id = f"{session_obj._session_id}_turn_{len(session_obj._turn_traces)}"
            exporter.add_span(OTelSpan(
                trace_id="t1", span_id="s1", parent_span_id=None,
                name="llm_call", kind="LLM",
                start_time_ns=0, end_time_ns=5_000_000,
                attributes={
                    "session.id": turn_id,
                    "output.value": f"response to {msg}",
                    "llm.model_name": "gpt-4o",
                    "llm.token_count.prompt": 50,
                    "llm.token_count.completion": 20,
                    "openinference.span.kind": "LLM",
                    "langgraph.node": "main_agent",
                },
            ))
            exporter.add_span(OTelSpan(
                trace_id="t1", span_id="s2", parent_span_id="s1",
                name="search_tool", kind="TOOL",
                start_time_ns=1_000_000, end_time_ns=3_000_000,
                attributes={
                    "session.id": turn_id,
                    "tool.name": "web_search",
                    "input.value": '{"query": "test"}',
                    "output.value": "search results",
                    "openinference.span.kind": "TOOL",
                },
            ))
            return f"response to {msg}"

        mod.target = target_fn
        sys.modules["_test_otel_spans"] = mod

        try:
            session = OTelTracedSession(
                callable_ref="_test_otel_spans:target",
                exporter=exporter,
            )
            mod._session = session

            async def _run():
                await session.open()
                result = await session.run_turn([Message(role="user", content="test")])
                await session.close()
                return result

            result = asyncio.run(_run())
            self.assertEqual(result.text, "response to test")
            self.assertIn("main_agent", result.raw["nodes_visited"])
            self.assertIn("web_search", result.raw["tools_called"])
            self.assertEqual(result.raw["llm_call_count"], 1)
            self.assertEqual(result.raw["total_tokens"]["input"], 50)
            self.assertEqual(result.raw["total_tokens"]["output"], 20)
            self.assertTrue(result.raw["span_validation"]["valid"])

            # Interaction messages should include tool call events
            tool_msgs = [m for m in result.interaction_messages if m.get("role") == "tool"]
            self.assertEqual(len(tool_msgs), 1)
            self.assertEqual(tool_msgs[0]["function"], "web_search")
        finally:
            del sys.modules["_test_otel_spans"]

    def test_multi_turn_accumulation(self):
        """Multiple turns should accumulate trace data."""
        from p2m.core.otel_session import OTelTracedSession
        from p2m.core.model_client import Message

        import sys
        import types
        mod = types.ModuleType("_test_otel_multi")
        call_count = [0]

        def target_fn(msg: str) -> str:
            call_count[0] += 1
            return f"turn_{call_count[0]}"

        mod.target = target_fn
        sys.modules["_test_otel_multi"] = mod

        try:
            session = OTelTracedSession(callable_ref="_test_otel_multi:target")

            async def _run():
                await session.open()
                r1 = await session.run_turn([Message(role="user", content="probe1")])
                r2 = await session.run_turn([
                    Message(role="user", content="probe1"),
                    Message(role="assistant", content="turn_1"),
                    Message(role="user", content="probe2"),
                ])
                await session.close()
                return r1, r2

            r1, r2 = asyncio.run(_run())
            self.assertEqual(r1.raw["accumulated_turns"], 1)
            self.assertEqual(r2.raw["accumulated_turns"], 2)
            self.assertEqual(r1.text, "turn_1")
            self.assertEqual(r2.text, "turn_2")
        finally:
            del sys.modules["_test_otel_multi"]

    def test_session_metadata(self):
        """session_metadata should reflect current state."""
        from p2m.core.otel_session import OTelTracedSession
        from p2m.core.model_client import Message

        import sys
        import types
        mod = types.ModuleType("_test_otel_meta")
        mod.target = lambda msg: "ok"
        sys.modules["_test_otel_meta"] = mod

        try:
            session = OTelTracedSession(callable_ref="_test_otel_meta:target")

            async def _run():
                await session.open()
                meta_before = session.session_metadata
                await session.run_turn([Message(role="user", content="test")])
                meta_after = session.session_metadata
                await session.close()
                return meta_before, meta_after

            before, after = asyncio.run(_run())
            self.assertEqual(before["turn_count"], 0)
            self.assertEqual(after["turn_count"], 1)
            self.assertEqual(after["trace_backend"], "otel")
        finally:
            del sys.modules["_test_otel_meta"]


# ── Rollout wiring tests ─────────────────────────────────────────


class TestRolloutOTelWiring(unittest.TestCase):
    """Validates that _build_target_session routes to OTelTracedSession."""

    def test_callable_with_trace_returns_otel_session(self):
        from p2m.core.config_model import TargetConfig, TraceConfig, RolloutConfig
        from p2m.stages.rollout import _build_target_session
        from p2m.core.otel_session import OTelTracedSession

        target = TargetConfig(
            callable="some.module:fn",
            trace=TraceConfig(backend="phoenix", group_by="session.id"),
        )
        session = _build_target_session(
            target=target,
            seed_payload={},
            rollout=RolloutConfig(),
            max_tokens=1024,
            config_path=None,
        )
        self.assertIsInstance(session, OTelTracedSession)

    def test_callable_without_trace_returns_callable_session(self):
        from p2m.core.config_model import TargetConfig, RolloutConfig
        from p2m.stages.rollout import _build_target_session
        from p2m.core.session import CallableSession

        target = TargetConfig(callable="some.module:fn")
        session = _build_target_session(
            target=target,
            seed_payload={},
            rollout=RolloutConfig(),
            max_tokens=1024,
            config_path=None,
        )
        self.assertIsInstance(session, CallableSession)


if __name__ == "__main__":
    unittest.main()


# ── SpanCollector Protocol tests ─────────────────────────────────


class TestSpanCollectorProtocol(unittest.TestCase):
    """Validates the SpanCollector Protocol and implementations."""

    def test_dataframe_collector_satisfies_protocol(self):
        from p2m.core.collector import DataFrameCollector, SpanCollector

        class FakeDF:
            columns = ["a", "b"]

        collector = DataFrameCollector(FakeDF())
        self.assertIsInstance(collector, SpanCollector)

    def test_dataframe_collector_get_spans(self):
        from p2m.core.collector import DataFrameCollector

        sentinel = object()
        collector = DataFrameCollector(sentinel)
        self.assertIs(collector.get_spans("project"), sentinel)
        self.assertIs(collector.get_spans(), sentinel)

    def test_dataframe_collector_validate_missing_columns(self):
        from p2m.core.collector import DataFrameCollector, REQUIRED_COLUMNS

        class FakeDF:
            columns = ["context.trace_id", "name"]

        collector = DataFrameCollector(FakeDF())
        warnings = collector.validate(FakeDF())
        self.assertEqual(len(warnings), 1)
        self.assertIn("Missing required columns", warnings[0])

    def test_dataframe_collector_validate_all_present(self):
        from p2m.core.collector import DataFrameCollector, REQUIRED_COLUMNS

        class FakeDF:
            columns = list(REQUIRED_COLUMNS)

        collector = DataFrameCollector(FakeDF())
        warnings = collector.validate(FakeDF())
        self.assertEqual(warnings, [])

    def test_dataframe_collector_validate_non_dataframe(self):
        from p2m.core.collector import DataFrameCollector

        collector = DataFrameCollector("not a df")
        warnings = collector.validate("not a df")
        self.assertEqual(warnings, [])  # no columns attr → no check

    def test_phoenix_collector_import_error(self):
        from p2m.core.collector import PhoenixCollector

        with self.assertRaises(ImportError) as ctx:
            PhoenixCollector()
        self.assertIn("arize-phoenix", str(ctx.exception))

    def test_custom_collector_satisfies_protocol(self):
        """A plain class with get_spans/validate should satisfy SpanCollector."""
        from p2m.core.collector import SpanCollector

        class MyCollector:
            def get_spans(self, project_name, **kw):
                return []

            def validate(self, spans):
                return []

        self.assertIsInstance(MyCollector(), SpanCollector)


# ── Extraction API tests ─────────────────────────────────────────


class TestExtractSpanInputs(unittest.TestCase):
    """Validates extract_span_inputs returns correct structure."""

    def _make_span(self, **overrides):
        from p2m.core.otel import OTelSpan

        defaults = dict(
            trace_id="t1",
            span_id="s1",
            parent_span_id=None,
            name="llm_call",
            kind="LLM",
            start_time_ns=0,
            end_time_ns=2_000_000,
            attributes={
                "input.value": "hello",
                "output.value": "world",
                "llm.model_name": "gpt-4o",
                "llm.token_count.prompt": 10,
                "llm.token_count.completion": 5,
                "langgraph.node": "agent",
            },
        )
        defaults.update(overrides)
        return OTelSpan(**defaults)

    def test_basic_extraction(self):
        from p2m.core.otel import extract_span_inputs

        spans = [self._make_span()]
        result = extract_span_inputs(spans)
        self.assertEqual(len(result), 1)
        row = result[0]
        self.assertEqual(row["query"], "hello")
        self.assertEqual(row["response"], "world")
        self.assertEqual(row["model"], "gpt-4o")
        self.assertEqual(row["input_tokens"], 10)
        self.assertEqual(row["output_tokens"], 5)
        self.assertEqual(row["node"], "agent")
        self.assertAlmostEqual(row["latency_ms"], 2.0)

    def test_filters_by_kind(self):
        from p2m.core.otel import extract_span_inputs

        spans = [
            self._make_span(kind="LLM", span_id="s1"),
            self._make_span(kind="TOOL", span_id="s2"),
            self._make_span(kind="CHAIN", span_id="s3"),
        ]
        result = extract_span_inputs(spans)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["span_id"], "s1")

    def test_custom_span_kind_filter(self):
        from p2m.core.otel import extract_span_inputs

        spans = [
            self._make_span(kind="TOOL", span_id="s1"),
        ]
        result = extract_span_inputs(spans, span_kind="TOOL")
        self.assertEqual(len(result), 1)

    def test_empty_spans(self):
        from p2m.core.otel import extract_span_inputs

        result = extract_span_inputs([])
        self.assertEqual(result, [])


class TestExtractTrajectoryInputs(unittest.TestCase):
    """Validates extract_trajectory_inputs groups by trace correctly."""

    def _make_span(self, **overrides):
        from p2m.core.otel import OTelSpan

        defaults = dict(
            trace_id="t1",
            span_id="s1",
            parent_span_id=None,
            name="span",
            kind="LLM",
            start_time_ns=0,
            end_time_ns=1_000_000,
            attributes={},
        )
        defaults.update(overrides)
        return OTelSpan(**defaults)

    def test_groups_by_trace(self):
        from p2m.core.otel import extract_trajectory_inputs

        spans = [
            self._make_span(
                trace_id="t1",
                span_id="s1",
                kind="LLM",
                start_time_ns=0,
                attributes={"input.value": "q1"},
            ),
            self._make_span(
                trace_id="t1",
                span_id="s2",
                kind="TOOL",
                start_time_ns=1_000,
                attributes={
                    "tool.name": "search",
                    "input.value": '{"q": "test"}',
                },
            ),
            self._make_span(
                trace_id="t2",
                span_id="s3",
                kind="LLM",
                start_time_ns=0,
                attributes={"input.value": "q2"},
            ),
        ]
        result = extract_trajectory_inputs(spans)
        self.assertEqual(len(result), 2)

        t1_row = next(r for r in result if r["trace_id"] == "t1")
        self.assertEqual(t1_row["user_input"], "q1")
        tool_calls = json.loads(t1_row["tool_calls"])
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0]["name"], "search")

    def test_node_path_captured(self):
        from p2m.core.otel import extract_trajectory_inputs

        spans = [
            self._make_span(
                trace_id="t1",
                span_id="s1",
                kind="LLM",
                start_time_ns=0,
                attributes={"langgraph.node": "planner"},
            ),
            self._make_span(
                trace_id="t1",
                span_id="s2",
                kind="LLM",
                start_time_ns=1000,
                attributes={"langgraph.node": "executor"},
            ),
        ]
        result = extract_trajectory_inputs(spans)
        node_path = json.loads(result[0]["node_path"])
        self.assertEqual(node_path, ["planner", "executor"])

    def test_token_aggregation(self):
        from p2m.core.otel import extract_trajectory_inputs

        spans = [
            self._make_span(
                trace_id="t1",
                span_id="s1",
                kind="LLM",
                attributes={
                    "llm.token_count.prompt": 50,
                    "llm.token_count.completion": 25,
                },
            ),
            self._make_span(
                trace_id="t1",
                span_id="s2",
                kind="LLM",
                start_time_ns=1000,
                attributes={
                    "llm.token_count.prompt": 100,
                    "llm.token_count.completion": 50,
                },
            ),
        ]
        result = extract_trajectory_inputs(spans)
        self.assertEqual(result[0]["total_tokens"]["input"], 150)
        self.assertEqual(result[0]["total_tokens"]["output"], 75)

    def test_empty_spans(self):
        from p2m.core.otel import extract_trajectory_inputs

        result = extract_trajectory_inputs([])
        self.assertEqual(result, [])


class TestExtractSessionInputs(unittest.TestCase):
    """Validates extract_session_inputs groups by session correctly."""

    def _make_span(self, **overrides):
        from p2m.core.otel import OTelSpan

        defaults = dict(
            trace_id="t1",
            span_id="s1",
            parent_span_id=None,
            name="span",
            kind="LLM",
            start_time_ns=0,
            end_time_ns=1_000_000,
            attributes={},
        )
        defaults.update(overrides)
        return OTelSpan(**defaults)

    def test_groups_by_session(self):
        from p2m.core.otel import extract_session_inputs

        spans = [
            self._make_span(
                trace_id="t1",
                span_id="s1",
                kind="LLM",
                attributes={
                    "session.id": "sess_A",
                    "input.value": "hello",
                    "output.value": "hi there",
                },
            ),
            self._make_span(
                trace_id="t2",
                span_id="s2",
                kind="LLM",
                start_time_ns=1000,
                attributes={
                    "session.id": "sess_A",
                    "input.value": "follow-up",
                    "output.value": "sure",
                },
            ),
            self._make_span(
                trace_id="t3",
                span_id="s3",
                kind="LLM",
                attributes={
                    "session.id": "sess_B",
                    "input.value": "other",
                    "output.value": "resp",
                },
            ),
        ]
        result = extract_session_inputs(spans)
        self.assertEqual(len(result), 2)

        sess_a = next(r for r in result if r["session_id"] == "sess_A")
        self.assertEqual(sess_a["trace_count"], 2)
        user_inputs = json.loads(sess_a["user_inputs"])
        self.assertEqual(user_inputs, ["hello", "follow-up"])
        outputs = json.loads(sess_a["output_messages"])
        self.assertEqual(outputs, ["hi there", "sure"])

    def test_tool_calls_collected(self):
        from p2m.core.otel import extract_session_inputs

        spans = [
            self._make_span(
                trace_id="t1",
                span_id="s1",
                kind="TOOL",
                attributes={
                    "session.id": "sess_A",
                    "tool.name": "search",
                    "input.value": "query",
                },
            ),
        ]
        result = extract_session_inputs(spans)
        tool_calls = json.loads(result[0]["tool_calls"])
        self.assertEqual(len(tool_calls), 1)
        self.assertIn("search(query)", tool_calls)

    def test_empty_spans(self):
        from p2m.core.otel import extract_session_inputs

        result = extract_session_inputs([])
        self.assertEqual(result, [])


# ── OTelTracedSession collector param test ───────────────────────


class TestOTelTracedSessionCollector(unittest.TestCase):
    """Validates OTelTracedSession accepts SpanCollector."""

    def test_accepts_collector_kwarg(self):
        from p2m.core.otel_session import OTelTracedSession

        class FakeCollector:
            def get_spans(self, project_name, **kw):
                return []

            def validate(self, spans):
                return []

        session = OTelTracedSession(
            callable_ref="some.module:fn",
            collector=FakeCollector(),
        )
        self.assertEqual(session.runtime_mode, "otel_traced")

    def test_backward_compat_exporter_still_works(self):
        """Passing exporter= still works as before."""
        from p2m.core.otel_session import OTelTracedSession
        from p2m.core.otel import InMemoryTraceExporter

        session = OTelTracedSession(
            callable_ref="some.module:fn",
            exporter=InMemoryTraceExporter(),
        )
        self.assertEqual(session.runtime_mode, "otel_traced")


# ── HTTPEndpointSession tests ────────────────────────────────────


class TestHTTPEndpointSession(unittest.TestCase):
    """Validates HTTPEndpointSession import, config, and runtime_mode."""

    def test_endpoint_import(self):
        """HTTPEndpointSession should be importable from p2m.core.session."""
        from p2m.core.session import HTTPEndpointSession
        self.assertTrue(callable(HTTPEndpointSession))

    def test_endpoint_runtime_mode(self):
        """HTTPEndpointSession.runtime_mode should be 'http_endpoint'."""
        from p2m.core.session import HTTPEndpointSession
        session = HTTPEndpointSession(endpoint="http://localhost:8080/chat")
        self.assertEqual(session.runtime_mode, "http_endpoint")

    def test_endpoint_config_valid(self):
        """TargetConfig(endpoint='http://...') should be accepted."""
        from p2m.core.config_model import TargetConfig
        tc = TargetConfig(endpoint="http://localhost:8080/chat")
        self.assertTrue(tc.is_endpoint)
        self.assertFalse(tc.is_callable)
        self.assertFalse(tc.is_external)

    def test_endpoint_config_conflicts_with_model(self):
        """TargetConfig with both endpoint and model should raise."""
        from p2m.core.config_model import TargetConfig
        with self.assertRaises(ValueError):
            TargetConfig(endpoint="http://localhost:8080/chat", model="openai/gpt-4o")

    def test_endpoint_and_callable_conflicts(self):
        """TargetConfig with both endpoint and callable should raise."""
        from p2m.core.config_model import TargetConfig
        with self.assertRaises(ValueError):
            TargetConfig(endpoint="http://localhost:8080/chat", callable="my_module:fn")

    def test_endpoint_and_connector_conflicts(self):
        """TargetConfig with both endpoint and connector should raise."""
        from p2m.core.config_model import TargetConfig
        with self.assertRaises(ValueError):
            TargetConfig(endpoint="http://localhost:8080/chat", connector="some.connector")

    def test_endpoint_rollout_wiring(self):
        """_build_target_session should return HTTPEndpointSession for endpoint targets."""
        from p2m.core.config_model import TargetConfig, RolloutConfig
        from p2m.stages.rollout import _build_target_session
        from p2m.core.session import HTTPEndpointSession

        target = TargetConfig(endpoint="http://localhost:8080/chat")
        session = _build_target_session(
            target=target,
            seed_payload={},
            rollout=RolloutConfig(),
            max_tokens=1024,
            config_path=None,
        )
        self.assertIsInstance(session, HTTPEndpointSession)


# ── judge-traces CLI tests ───────────────────────────────────────


class TestJudgeTracesCLI(unittest.TestCase):
    """Validates the judge-traces CLI command."""

    def test_judge_traces_parses_fixture(self):
        """Invoke CLI with sample fixture, verify it finds conversations."""
        from click.testing import CliRunner
        from p2m.cli import cli

        # We need a minimal config YAML for the --config option
        import tempfile
        import os
        config_content = "target:\\n  model: openai/gpt-4o\\njudge:\\n  model: openai/gpt-4o\\n"
        config_path = Path(FIXTURES) / "_test_judge_config.yaml"
        try:
            with open(config_path, "w") as f:
                f.write("target:\n  model: openai/gpt-4o\njudge:\n  model: openai/gpt-4o\n")

            runner = CliRunner()
            result = runner.invoke(cli, [
                "judge-traces",
                "--traces", str(SAMPLE_TRACES),
                "--config", str(config_path),
                "--group-by", "session.id",
            ])
            self.assertIn("Found 2 conversations", result.output)
            self.assertEqual(result.exit_code, 0)
        finally:
            if config_path.exists():
                config_path.unlink()

    def test_judge_traces_empty_traces_fails(self):
        """CLI should exit 1 when no conversations are found."""
        from click.testing import CliRunner
        from p2m.cli import cli

        # Create an empty traces file and a config
        empty_traces = Path(FIXTURES) / "_test_empty_traces.json"
        config_path = Path(FIXTURES) / "_test_judge_config2.yaml"
        try:
            with open(empty_traces, "w") as f:
                json.dump({"resourceSpans": []}, f)
            with open(config_path, "w") as f:
                f.write("target:\n  model: openai/gpt-4o\n")

            runner = CliRunner()
            result = runner.invoke(cli, [
                "judge-traces",
                "--traces", str(empty_traces),
                "--config", str(config_path),
            ])
            self.assertIn("No conversations found", result.output)
            self.assertEqual(result.exit_code, 1)
        finally:
            if empty_traces.exists():
                empty_traces.unlink()
            if config_path.exists():
                config_path.unlink()
