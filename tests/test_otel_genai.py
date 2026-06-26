# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for OTel GenAI semantic-convention (gen_ai.*) span parsing.

ASSERT's OTel parser historically only understood OpenInference conventions
(openinference.span.kind, tool.name, input.value). These tests pin the
behavior for the newer, increasingly-standard OpenTelemetry GenAI semantic
conventions (gen_ai.*) emitted by a growing number of agent runtimes.

Spec (verified against the OpenTelemetry GenAI semantic conventions):
- Model spans use gen_ai.operation.name in {chat, generate_content,
  text_completion}, with gen_ai.request.model / gen_ai.response.model and
  gen_ai.usage.input_tokens / gen_ai.usage.output_tokens.
- Tool spans use gen_ai.operation.name == "execute_tool" with gen_ai.tool.name,
  gen_ai.tool.call.id, and opt-in gen_ai.tool.call.arguments /
  gen_ai.tool.call.result.
- Content may be attribute-carried (gen_ai.output.messages on the span) or
  event-carried (gen_ai.choice / gen_ai.tool.message span events).
- openclaw.content.* is an OPTIONAL vendor enrichment layer; the generic
  gen_ai.* mapping must stand on its own without it.

These tests assert the parser emits the same {metadata, events, raw} inference
rows (tool_call / add_message shapes) that the OpenInference path already
produces, so the judge/viewer consume them unchanged.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from assert_ai.core.otel import parse_otel_traces

FIXTURES = Path(__file__).parent / "fixtures"
GENAI_TRACES = FIXTURES / "genai_otel_traces.json"
GENAI_OPENCLAW_TRACES = FIXTURES / "genai_openclaw_enrichment_traces.json"

GROUP = "gen_ai.conversation.id"


def _tool_events(row):
    return [e for e in row["events"] if e["actor"] == "tool"]


def _target_events(row):
    return [e for e in row["events"] if e["actor"] == "target"]


def _row(rows, conv_id):
    return next(r for r in rows if r["metadata"]["session_id"] == conv_id)


# ── gen_ai chat/model span → add_message + usage/model metadata ──────


class TestGenAIChatSpan(unittest.TestCase):
    """A gen_ai chat span should map to an assistant add_message row with
    model + token metadata, mirroring the OpenInference LLM path."""

    def setUp(self):
        self.rows = parse_otel_traces(GENAI_TRACES, group_by=GROUP)

    def test_conversation_grouping(self):
        conv_ids = {r["metadata"]["session_id"] for r in self.rows}
        self.assertEqual(conv_ids, {"conv_weather", "conv_tokyo"})

    def test_chat_span_emits_assistant_message(self):
        weather = _row(self.rows, "conv_weather")
        targets = _target_events(weather)
        self.assertTrue(targets, "gen_ai chat span produced no assistant message")
        contents = " ".join(
            e["edit"]["message"]["content"] for e in targets
        )
        self.assertIn("sunny in Paris", contents)

    def test_chat_span_carries_model_and_tokens(self):
        weather = _row(self.rows, "conv_weather")
        msg = _target_events(weather)[0]
        # response.model preferred over request.model
        self.assertEqual(msg["raw"]["_model"], "gpt-4o-2024-08-06")
        self.assertEqual(msg["raw"]["_tokens"]["input"], 120)
        self.assertEqual(msg["raw"]["_tokens"]["output"], 45)

    def test_aggregate_usage(self):
        weather = _row(self.rows, "conv_weather")
        agg = weather["raw"]
        self.assertEqual(agg["llm_call_count"], 1)
        self.assertEqual(agg["total_tokens"]["input"], 120)
        self.assertEqual(agg["total_tokens"]["output"], 45)

    def test_request_model_used_when_no_response_model(self):
        tokyo = _row(self.rows, "conv_tokyo")
        msgs = _target_events(tokyo)
        self.assertTrue(msgs)
        self.assertEqual(msgs[0]["raw"]["_model"], "gpt-4o")

    def test_inference_row_schema_preserved(self):
        for row in self.rows:
            self.assertIn("metadata", row)
            self.assertIn("events", row)
            self.assertIn("raw", row)
            self.assertEqual(row["metadata"]["type"], "otel_import")
            self.assertEqual(row["metadata"]["runtime_mode"], "otel_traced")


# ── gen_ai execute_tool span → tool_call row ────────────────────────


class TestGenAIExecuteToolSpan(unittest.TestCase):
    """An execute_tool span should map to a tool_call event with parsed
    args, result, and call id — not be dropped as UNKNOWN."""

    def setUp(self):
        self.rows = parse_otel_traces(GENAI_TRACES, group_by=GROUP)
        self.weather = _row(self.rows, "conv_weather")

    def test_tool_call_emitted(self):
        tools = _tool_events(self.weather)
        names = [e["edit"]["tool_name"] for e in tools]
        self.assertIn("get_weather", names)

    def test_tool_args_parsed(self):
        tool = next(
            e for e in _tool_events(self.weather)
            if e["edit"]["tool_name"] == "get_weather"
        )
        self.assertEqual(tool["edit"]["tool_args"]["location"], "Paris")
        self.assertEqual(tool["edit"]["tool_args"]["unit"], "celsius")

    def test_tool_result_captured(self):
        tool = next(
            e for e in _tool_events(self.weather)
            if e["edit"]["tool_name"] == "get_weather"
        )
        result = tool["edit"]["tool_result"]
        result_text = result if isinstance(result, str) else json.dumps(result)
        self.assertIn("sunny", result_text)

    def test_tool_call_id_preserved(self):
        tool = next(
            e for e in _tool_events(self.weather)
            if e["edit"]["tool_name"] == "get_weather"
        )
        # call id surfaced somewhere on the edit so the judge can correlate
        edit = tool["edit"]
        call_id = edit.get("tool_call_id") or (edit.get("raw") or {}).get("_tool_call_id")
        self.assertEqual(call_id, "call_abc123")

    def test_aggregate_tools_called(self):
        self.assertIn("get_weather", self.weather["raw"]["tools_called"])


# ── Event-carried content (gen_ai.choice / gen_ai.tool.message) ──────


class TestGenAIEventCarriedContent(unittest.TestCase):
    """The GenAI conventions put a lot of content in span events rather than
    attributes. A chat span with a gen_ai.choice event containing tool_calls,
    plus a gen_ai.tool.message result event, must still yield tool_call rows."""

    def setUp(self):
        self.rows = parse_otel_traces(GENAI_TRACES, group_by=GROUP)
        self.tokyo = _row(self.rows, "conv_tokyo")

    def test_tool_call_from_choice_event(self):
        tools = _tool_events(self.tokyo)
        names = [e["edit"]["tool_name"] for e in tools]
        self.assertIn("search_flights", names)

    def test_event_tool_args_parsed(self):
        tool = next(
            e for e in _tool_events(self.tokyo)
            if e["edit"]["tool_name"] == "search_flights"
        )
        self.assertEqual(tool["edit"]["tool_args"]["destination"], "NRT")

    def test_event_tool_result_matched_by_id(self):
        tool = next(
            e for e in _tool_events(self.tokyo)
            if e["edit"]["tool_name"] == "search_flights"
        )
        result = tool["edit"]["tool_result"]
        result_text = result if isinstance(result, str) else json.dumps(result)
        self.assertIn("ANA", result_text)

    def test_assistant_text_from_choice_event(self):
        targets = _target_events(self.tokyo)
        contents = " ".join(e["edit"]["message"]["content"] for e in targets)
        self.assertIn("look up flights to Tokyo", contents)


# ── openclaw.content.* enrichment (optional vendor add-on) ──────────


class TestGenAIOpenClawEnrichment(unittest.TestCase):
    """When generic gen_ai.* opt-in content attrs are absent, the parser
    should fall back to the openclaw.content.* enrichment for fidelity —
    but only as a graceful add-on layered on the generic gen_ai mapping."""

    def setUp(self):
        self.rows = parse_otel_traces(GENAI_OPENCLAW_TRACES, group_by=GROUP)
        self.row = _row(self.rows, "conv_openclaw")

    def test_tool_input_from_enrichment(self):
        tool = next(
            e for e in _tool_events(self.row)
            if e["edit"]["tool_name"] == "book_flight"
        )
        self.assertEqual(tool["edit"]["tool_args"]["flight"], "ANA123")

    def test_tool_output_from_enrichment(self):
        tool = next(
            e for e in _tool_events(self.row)
            if e["edit"]["tool_name"] == "book_flight"
        )
        result = tool["edit"]["tool_result"]
        result_text = result if isinstance(result, str) else json.dumps(result)
        self.assertIn("AB12CD", result_text)

    def test_output_messages_from_enrichment(self):
        targets = _target_events(self.row)
        contents = " ".join(e["edit"]["message"]["content"] for e in targets)
        self.assertIn("booked your flight", contents)

    def test_generic_genai_metadata_still_present(self):
        # The headline must stand on generic gen_ai.* alone: model/usage come
        # from gen_ai.*, not the vendor enrichment.
        agg = self.row["raw"]
        self.assertEqual(agg["total_tokens"]["input"], 300)
        self.assertEqual(agg["total_tokens"]["output"], 80)
        self.assertIn("book_flight", agg["tools_called"])


# ── Convention coexistence (don't regress OpenInference) ────────────


class TestConventionCoexistence(unittest.TestCase):
    """OpenInference and gen_ai spans must coexist. A single trace file with
    one OpenInference LLM span and one gen_ai execute_tool span should yield
    both an assistant message and a tool_call."""

    def _write(self, doc):
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(doc, tmp)
        tmp.close()
        # Unlink after the test (even on failure) so fixtures don't leak across
        # repeated or parallel runs.
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
        return tmp.name

    def test_mixed_conventions_in_one_trace(self):
        doc = {
            "resourceSpans": [{"scopeSpans": [{"spans": [
                {
                    "traceId": "mix1", "spanId": "oi_1", "name": "llm",
                    "startTimeUnixNano": 1000000000, "endTimeUnixNano": 2000000000,
                    "attributes": [
                        {"key": "openinference.span.kind", "value": {"stringValue": "LLM"}},
                        {"key": "session.id", "value": {"stringValue": "mixconv"}},
                        {"key": "output.value", "value": {"stringValue": "OpenInference answer."}},
                        {"key": "llm.model_name", "value": {"stringValue": "gpt-4o"}},
                    ],
                },
                {
                    "traceId": "mix1", "spanId": "ga_1", "name": "execute_tool calc",
                    "startTimeUnixNano": 2100000000, "endTimeUnixNano": 3000000000,
                    "attributes": [
                        {"key": "gen_ai.operation.name", "value": {"stringValue": "execute_tool"}},
                        {"key": "gen_ai.tool.name", "value": {"stringValue": "calc"}},
                        {"key": "session.id", "value": {"stringValue": "mixconv"}},
                        {"key": "gen_ai.tool.call.arguments", "value": {"stringValue": "{\"x\": 2}"}},
                        {"key": "gen_ai.tool.call.result", "value": {"stringValue": "4"}},
                    ],
                },
            ]}]}]
        }
        path = self._write(doc)
        rows = parse_otel_traces(path, group_by="session.id")
        row = _row(rows, "mixconv")

        targets = _target_events(row)
        tools = _tool_events(row)
        self.assertTrue(
            any("OpenInference answer" in e["edit"]["message"]["content"] for e in targets),
            "OpenInference LLM span regressed",
        )
        self.assertIn("calc", [e["edit"]["tool_name"] for e in tools])


# ── Span validation for gen_ai spans ────────────────────────────────


class TestGenAISpanValidation(unittest.TestCase):
    """validate_spans must judge gen_ai spans by gen_ai.* attributes, not the
    OpenInference attribute names, so a well-formed gen_ai trace is clean and a
    deficient one warns on the right keys."""

    def _span(self, attrs, kind, events=None):
        from assert_ai.core.otel import OTelSpan
        return OTelSpan(
            trace_id="t", span_id="s1", parent_span_id=None,
            name="span", kind=kind,
            start_time_ns=0, end_time_ns=1_000_000,
            attributes=attrs, events=events or [],
        )

    def test_complete_genai_chat_span_is_valid(self):
        from assert_ai.core.otel import validate_spans
        span = self._span({
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": "openai",
            "gen_ai.request.model": "gpt-4o",
            "gen_ai.usage.input_tokens": 10,
            "gen_ai.usage.output_tokens": 5,
            "gen_ai.output.messages": "[{\"role\": \"assistant\", \"content\": \"hi\"}]",
        }, kind="LLM")
        result = validate_spans([span])
        self.assertTrue(result.valid, result.warnings)
        self.assertEqual(result.warnings, [])

    def test_complete_genai_tool_span_is_valid(self):
        from assert_ai.core.otel import validate_spans
        span = self._span({
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "search",
        }, kind="TOOL")
        result = validate_spans([span])
        self.assertTrue(result.valid, result.warnings)

    def test_genai_chat_span_warns_on_missing_model(self):
        from assert_ai.core.otel import validate_spans
        span = self._span({
            "gen_ai.operation.name": "chat",
            "gen_ai.output.messages": "[{\"role\": \"assistant\", \"content\": \"hi\"}]",
            "gen_ai.usage.input_tokens": 10,
        }, kind="LLM")
        result = validate_spans([span])
        self.assertFalse(result.valid)
        self.assertTrue(any("gen_ai.request.model" in w for w in result.warnings))

    def test_genai_tool_span_warns_on_missing_tool_name(self):
        from assert_ai.core.otel import validate_spans
        span = self._span({
            "gen_ai.operation.name": "execute_tool",
        }, kind="TOOL")
        result = validate_spans([span])
        self.assertFalse(result.valid)
        self.assertTrue(any("gen_ai.tool.name" in w for w in result.warnings))

    def test_genai_chat_span_does_not_warn_on_openinference_keys(self):
        from assert_ai.core.otel import validate_spans
        # A complete gen_ai span must not be flagged for missing OpenInference
        # attribute names (output.value, llm.model_name, tool.name).
        span = self._span({
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": "gpt-4o",
            "gen_ai.usage.output_tokens": 5,
            "gen_ai.output.messages": "[{\"role\": \"assistant\", \"content\": \"hi\"}]",
        }, kind="LLM")
        result = validate_spans([span])
        self.assertFalse(any("output.value" in w for w in result.warnings))
        self.assertFalse(any("llm.model_name" in w for w in result.warnings))


# ── Aggregate consistency across conventions (review follow-ups) ─────


def _write_otlp_tempfile(test_case, doc):
    """Write an OTLP-JSON doc to a temp file that is auto-removed after the test.

    Registers an addCleanup so the file is unlinked even on assertion failure,
    avoiding leaked fixtures across repeated/parallel test runs.
    """
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    )
    json.dump(doc, tmp)
    tmp.close()
    test_case.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
    return tmp.name


class TestGenAIAggregateConsistency(unittest.TestCase):
    """gen_ai aggregate metadata must match the OpenInference conventions:
    total_latency_ms aggregates LLM-span latency only, and node attribution
    honors langgraph.node when present.
    """

    def test_total_latency_excludes_tool_spans(self):
        # One model span (latency 1000ms) + one tool span (latency 500ms).
        # OpenInference only accumulates LLM-span latency, so the gen_ai path
        # must do the same: total_latency_ms == model-span latency only.
        doc = {
            "resourceSpans": [{"scopeSpans": [{"spans": [
                {
                    "traceId": "lat", "spanId": "m1", "name": "chat gpt-4o",
                    "startTimeUnixNano": 0, "endTimeUnixNano": 1_000_000_000,
                    "attributes": [
                        {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
                        {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4o"}},
                        {"key": "session.id", "value": {"stringValue": "lat_sess"}},
                        {"key": "gen_ai.output.messages",
                         "value": {"stringValue": "[{\"role\": \"assistant\", \"content\": \"ok\"}]"}},
                    ],
                },
                {
                    "traceId": "lat", "spanId": "t1", "name": "execute_tool calc",
                    "startTimeUnixNano": 1_000_000_000, "endTimeUnixNano": 1_500_000_000,
                    "attributes": [
                        {"key": "gen_ai.operation.name", "value": {"stringValue": "execute_tool"}},
                        {"key": "gen_ai.tool.name", "value": {"stringValue": "calc"}},
                        {"key": "session.id", "value": {"stringValue": "lat_sess"}},
                    ],
                },
            ]}]}]
        }
        path = _write_otlp_tempfile(self, doc)
        rows = parse_otel_traces(path, group_by="session.id")
        agg = _row(rows, "lat_sess")["raw"]
        self.assertEqual(agg["total_latency_ms"], 1000.0)

    def test_langgraph_node_attribution_honored(self):
        # A gen_ai model span that also carries langgraph.node must attribute
        # the node by that key (not the span name), matching OpenInference.
        doc = {
            "resourceSpans": [{"scopeSpans": [{"spans": [{
                "traceId": "nd", "spanId": "m1", "name": "chat gpt-4o",
                "startTimeUnixNano": 0, "endTimeUnixNano": 1_000_000,
                "attributes": [
                    {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
                    {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4o"}},
                    {"key": "langgraph.node", "value": {"stringValue": "planner"}},
                    {"key": "session.id", "value": {"stringValue": "nd_sess"}},
                    {"key": "gen_ai.output.messages",
                     "value": {"stringValue": "[{\"role\": \"assistant\", \"content\": \"hi\"}]"}},
                ],
            }]}]}]
        }
        path = _write_otlp_tempfile(self, doc)
        rows = parse_otel_traces(path, group_by="session.id")
        agg = _row(rows, "nd_sess")["raw"]
        self.assertIn("planner", agg["nodes_visited"])
        self.assertNotIn("chat gpt-4o", agg["nodes_visited"])


class TestGenAIZeroTokenValidation(unittest.TestCase):
    """Token counts of 0 are valid values, not missing attributes."""

    def _span(self, attrs, kind="LLM", events=None):
        from assert_ai.core.otel import OTelSpan
        return OTelSpan(
            trace_id="t", span_id="s1", parent_span_id=None,
            name="span", kind=kind,
            start_time_ns=0, end_time_ns=1_000_000,
            attributes=attrs, events=events or [],
        )

    def test_zero_token_counts_not_flagged_missing(self):
        from assert_ai.core.otel import validate_spans
        span = self._span({
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": "gpt-4o",
            "gen_ai.usage.input_tokens": 0,
            "gen_ai.usage.output_tokens": 0,
            "gen_ai.output.messages": "[{\"role\": \"assistant\", \"content\": \"hi\"}]",
        })
        result = validate_spans([span])
        self.assertFalse(
            any("token counts" in w for w in result.warnings),
            f"0 token counts are present and valid, not missing: {result.warnings}",
        )
        self.assertTrue(result.valid, result.warnings)

    def test_truly_missing_token_counts_still_flagged(self):
        from assert_ai.core.otel import validate_spans
        span = self._span({
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": "gpt-4o",
            "gen_ai.output.messages": "[{\"role\": \"assistant\", \"content\": \"hi\"}]",
        })
        result = validate_spans([span])
        self.assertTrue(any("token counts" in w for w in result.warnings))


class TestGenAIGracefulDegradation(unittest.TestCase):
    """Missing optional gen_ai fields must not crash or drop the span."""

    def test_chat_span_without_content_or_usage(self):
        doc = {
            "resourceSpans": [{"scopeSpans": [{"spans": [{
                "traceId": "t_min", "spanId": "s_min", "name": "chat gpt-4o",
                "startTimeUnixNano": 1_000_000_000, "endTimeUnixNano": 1_100_000_000,
                "attributes": [
                    {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
                    {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4o"}},
                    {"key": "session.id", "value": {"stringValue": "sess_min"}},
                ],
            }]}]}]
        }
        path = _write_otlp_tempfile(self, doc)
        rows = parse_otel_traces(path, group_by="session.id")
        self.assertEqual(len(rows), 1)
        # Counted as an LLM call even though it emitted no message content.
        self.assertEqual(rows[0]["raw"]["llm_call_count"], 1)

    def test_execute_tool_span_with_only_name(self):
        doc = {
            "resourceSpans": [{"scopeSpans": [{"spans": [{
                "traceId": "t_tool", "spanId": "s_tool", "name": "execute_tool lookup",
                "startTimeUnixNano": 1_000_000_000, "endTimeUnixNano": 1_100_000_000,
                "attributes": [
                    {"key": "gen_ai.operation.name", "value": {"stringValue": "execute_tool"}},
                    {"key": "gen_ai.tool.name", "value": {"stringValue": "lookup"}},
                    {"key": "session.id", "value": {"stringValue": "sess_tool"}},
                ],
            }]}]}]
        }
        path = _write_otlp_tempfile(self, doc)
        rows = parse_otel_traces(path, group_by="session.id")
        tool_events = [e for e in rows[0]["events"] if e["actor"] == "tool"]
        self.assertEqual(len(tool_events), 1)
        self.assertEqual(tool_events[0]["edit"]["tool_name"], "lookup")
        self.assertEqual(tool_events[0]["edit"]["tool_args"], {})
        self.assertEqual(tool_events[0]["edit"]["tool_result"], "")


class TestLiveExporterEventCarriedContent(unittest.TestCase):
    """LiveOTelExporter.export_session must preserve span events so the gen_ai
    path (which carries tool calls/results in events) is not silently dropped
    on the in-process live path the way it would be if events were omitted.
    """

    def _fake_sdk_span(self):
        from types import SimpleNamespace

        choice_event = SimpleNamespace(
            name="gen_ai.choice",
            attributes={
                "message": json.dumps({
                    "role": "assistant",
                    "content": "Looking that up.",
                    "tool_calls": [{
                        "id": "call_live1", "type": "function",
                        "function": {"name": "search", "arguments": "{\"q\": \"x\"}"},
                    }],
                }),
            },
        )
        return SimpleNamespace(
            name="chat gpt-4o",
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": "gpt-4o",
                "session.id": "live_sess",
            },
            context=SimpleNamespace(trace_id=0x1, span_id=0x2),
            parent=None,
            start_time=0,
            end_time=1_000_000,
            events=[choice_event],
        )

    def test_export_session_preserves_events(self):
        from assert_ai.core.otel import LiveOTelExporter
        from types import SimpleNamespace

        holder = SimpleNamespace(spans=[self._fake_sdk_span()])
        prev = LiveOTelExporter._sdk_exporter
        LiveOTelExporter._sdk_exporter = holder
        self.addCleanup(lambda: setattr(LiveOTelExporter, "_sdk_exporter", prev))

        spans = LiveOTelExporter().export_session("live_sess")
        self.assertEqual(len(spans), 1)
        self.assertTrue(
            spans[0].events,
            "live-exported span dropped its events; event-carried gen_ai "
            "tool calls/results would be lost",
        )

    def test_export_session_events_feed_genai_tool_calls(self):
        from assert_ai.core.otel import LiveOTelExporter, _spans_to_events
        from types import SimpleNamespace

        holder = SimpleNamespace(spans=[self._fake_sdk_span()])
        prev = LiveOTelExporter._sdk_exporter
        LiveOTelExporter._sdk_exporter = holder
        self.addCleanup(lambda: setattr(LiveOTelExporter, "_sdk_exporter", prev))

        spans = LiveOTelExporter().export_session("live_sess")
        events, _ = _spans_to_events(spans)
        tool_names = [e["edit"]["tool_name"] for e in events if e["actor"] == "tool"]
        self.assertIn("search", tool_names)


class TestSdkEventValueConversion(unittest.TestCase):
    """_value_to_otlp must round-trip the value shapes _flatten_attributes reads,
    including arrayValue, so live-exported event attributes match the file-based
    OTLP-JSON path instead of stringifying sequences.
    """

    def test_list_attribute_preserved_as_arrayvalue(self):
        from assert_ai.core.otel import _value_to_otlp, _flatten_attributes

        otlp = _value_to_otlp(["a", "b", "c"])
        self.assertIn("arrayValue", otlp)
        # Round-trip through the consumer the parser actually uses.
        flat = _flatten_attributes([{"key": "tags", "value": otlp}])
        self.assertEqual(flat["tags"], ["a", "b", "c"])

    def test_mixed_scalar_list_round_trips(self):
        from assert_ai.core.otel import _value_to_otlp, _flatten_attributes

        otlp = _value_to_otlp([1, 2, 3])
        flat = _flatten_attributes([{"key": "nums", "value": otlp}])
        # _flatten_attributes coerces intValue elements via _extract_value, which
        # returns the raw string form — matching the file-based path exactly.
        self.assertEqual(flat["nums"], ["1", "2", "3"])

    def test_scalars_unchanged(self):
        from assert_ai.core.otel import _value_to_otlp

        self.assertEqual(_value_to_otlp("hi"), {"stringValue": "hi"})
        self.assertEqual(_value_to_otlp(True), {"boolValue": True})
        self.assertEqual(_value_to_otlp(7), {"intValue": "7"})
        self.assertEqual(_value_to_otlp(1.5), {"doubleValue": 1.5})

    def test_live_event_list_attribute_survives_export(self):
        from types import SimpleNamespace
        from assert_ai.core.otel import LiveOTelExporter

        evt = SimpleNamespace(name="gen_ai.choice", attributes={"labels": ["x", "y"]})
        sdk_span = SimpleNamespace(
            name="chat", attributes={"gen_ai.operation.name": "chat", "session.id": "s"},
            context=SimpleNamespace(trace_id=0x1, span_id=0x2),
            parent=None, start_time=0, end_time=1_000_000, events=[evt],
        )
        holder = SimpleNamespace(spans=[sdk_span])
        prev = LiveOTelExporter._sdk_exporter
        LiveOTelExporter._sdk_exporter = holder
        self.addCleanup(lambda: setattr(LiveOTelExporter, "_sdk_exporter", prev))

        spans = LiveOTelExporter().export_session("s")
        # The event attribute survives as a structured list, not a stringified one.
        from assert_ai.core.otel import _flatten_attributes
        evt_attrs = _flatten_attributes(spans[0].events[0]["attributes"])
        self.assertEqual(evt_attrs["labels"], ["x", "y"])


class TestGenAIToolCallReviewFollowups(unittest.TestCase):
    """Review follow-ups around GenAI tool-call correlation and span events."""

    def test_execute_tool_span_binds_result_to_choice_call_without_duplicate(self):
        doc = {
            "resourceSpans": [{"scopeSpans": [{"spans": [
                {
                    "traceId": "stitch", "spanId": "chat1", "name": "chat",
                    "startTimeUnixNano": 0, "endTimeUnixNano": 1_000_000,
                    "attributes": [
                        {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
                        {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4o"}},
                        {"key": "session.id", "value": {"stringValue": "stitch_sess"}},
                    ],
                    "events": [{
                        "name": "gen_ai.choice",
                        "attributes": [{"key": "message", "value": {"stringValue": json.dumps({
                            "role": "assistant",
                            "content": "Calling lookup.",
                            "tool_calls": [{
                                "id": "call_same", "type": "function",
                                "function": {"name": "lookup", "arguments": "{\"q\": \"x\"}"},
                            }],
                        })}}],
                    }],
                },
                {
                    "traceId": "stitch", "spanId": "tool1", "name": "execute_tool lookup",
                    "startTimeUnixNano": 1_000_000, "endTimeUnixNano": 2_000_000,
                    "attributes": [
                        {"key": "gen_ai.operation.name", "value": {"stringValue": "execute_tool"}},
                        {"key": "gen_ai.tool.name", "value": {"stringValue": "lookup"}},
                        {"key": "gen_ai.tool.call.id", "value": {"stringValue": "call_same"}},
                        {"key": "gen_ai.tool.call.arguments", "value": {"stringValue": "{\"q\": \"x\"}"}},
                        {"key": "gen_ai.tool.call.result", "value": {"stringValue": "{\"value\": \"done\"}"}},
                        {"key": "session.id", "value": {"stringValue": "stitch_sess"}},
                    ],
                },
            ]}]}]
        }
        path = _write_otlp_tempfile(self, doc)
        rows = parse_otel_traces(path, group_by="session.id")
        tools = [
            e for e in _tool_events(_row(rows, "stitch_sess"))
            if e["edit"].get("tool_call_id") == "call_same"
        ]
        self.assertEqual(len(tools), 1, tools)
        self.assertEqual(tools[0]["edit"]["tool_args"], {"q": "x"})
        self.assertIn("done", tools[0]["edit"]["tool_result"])

    def test_repeated_call_ids_bind_results_in_fifo_order(self):
        def choice(q):
            return {
                "name": "gen_ai.choice",
                "attributes": [{"key": "message", "value": {"stringValue": json.dumps({
                    "role": "assistant",
                    "tool_calls": [{
                        "id": "dup", "type": "function",
                        "function": {"name": "lookup", "arguments": json.dumps({"q": q})},
                    }],
                })}}],
            }

        def result(value):
            return {
                "name": "gen_ai.tool.message",
                "attributes": [
                    {"key": "id", "value": {"stringValue": "dup"}},
                    {"key": "content", "value": {"stringValue": value}},
                ],
            }

        doc = {
            "resourceSpans": [{"scopeSpans": [{"spans": [{
                "traceId": "fifo", "spanId": "chat1", "name": "chat",
                "startTimeUnixNano": 0, "endTimeUnixNano": 1_000_000,
                "attributes": [
                    {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
                    {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4o"}},
                    {"key": "session.id", "value": {"stringValue": "fifo_sess"}},
                ],
                "events": [choice("first"), choice("second"), result("first-result"), result("second-result")],
            }]}]}]
        }
        path = _write_otlp_tempfile(self, doc)
        rows = parse_otel_traces(path, group_by="session.id")
        tools = _tool_events(_row(rows, "fifo_sess"))
        self.assertEqual([t["edit"]["tool_args"] for t in tools], [{"q": "first"}, {"q": "second"}])
        self.assertEqual([t["edit"]["tool_result"] for t in tools], ["first-result", "second-result"])

    def test_output_messages_tool_call_parts_emit_tool_call(self):
        messages = [{
            "role": "assistant",
            "parts": [
                {"type": "text", "content": "Need weather."},
                {
                    "type": "tool_call",
                    "id": "part_call",
                    "name": "get_weather",
                    "arguments": {"location": "Paris"},
                },
            ],
        }]
        doc = {
            "resourceSpans": [{"scopeSpans": [{"spans": [{
                "traceId": "parts", "spanId": "chat1", "name": "chat",
                "startTimeUnixNano": 0, "endTimeUnixNano": 1_000_000,
                "attributes": [
                    {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
                    {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4o"}},
                    {"key": "gen_ai.output.messages", "value": {"stringValue": json.dumps(messages)}},
                    {"key": "session.id", "value": {"stringValue": "parts_sess"}},
                ],
            }]}]}]
        }
        path = _write_otlp_tempfile(self, doc)
        rows = parse_otel_traces(path, group_by="session.id")
        tools = _tool_events(_row(rows, "parts_sess"))
        self.assertEqual(len(tools), 1, tools)
        self.assertEqual(tools[0]["edit"]["tool_name"], "get_weather")
        self.assertEqual(tools[0]["edit"]["tool_args"], {"location": "Paris"})
        self.assertEqual(tools[0]["edit"].get("tool_call_id"), "part_call")

    def test_non_model_genai_operations_do_not_count_or_duplicate_outputs(self):
        messages = [{"role": "assistant", "content": "Child answer."}]
        doc = {
            "resourceSpans": [{"scopeSpans": [{"spans": [
                {
                    "traceId": "agent", "spanId": "agent1", "name": "invoke_agent",
                    "startTimeUnixNano": 0, "endTimeUnixNano": 3_000_000,
                    "attributes": [
                        {"key": "gen_ai.operation.name", "value": {"stringValue": "invoke_agent"}},
                        {"key": "langgraph.node", "value": {"stringValue": "agent"}},
                        {"key": "gen_ai.output.messages", "value": {"stringValue": json.dumps(messages)}},
                        {"key": "session.id", "value": {"stringValue": "agent_sess"}},
                    ],
                },
                {
                    "traceId": "agent", "spanId": "chat1", "parentSpanId": "agent1", "name": "chat",
                    "startTimeUnixNano": 1_000_000, "endTimeUnixNano": 2_000_000,
                    "attributes": [
                        {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
                        {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4o"}},
                        {"key": "langgraph.node", "value": {"stringValue": "model"}},
                        {"key": "gen_ai.output.messages", "value": {"stringValue": json.dumps(messages)}},
                        {"key": "session.id", "value": {"stringValue": "agent_sess"}},
                    ],
                },
            ]}]}]
        }
        path = _write_otlp_tempfile(self, doc)
        rows = parse_otel_traces(path, group_by="session.id")
        row = _row(rows, "agent_sess")
        self.assertEqual(row["raw"]["llm_call_count"], 1)
        self.assertEqual(row["raw"]["total_latency_ms"], 1.0)
        self.assertEqual(row["raw"]["nodes_visited"], ["agent", "model"])
        target_text = [e["edit"]["message"]["content"] for e in _target_events(row)]
        self.assertEqual(target_text, ["Child answer."])

    def test_agent_span_events_can_emit_tool_calls_without_llm_count(self):
        doc = {
            "resourceSpans": [{"scopeSpans": [{"spans": [{
                "traceId": "agent_evt", "spanId": "agent1", "name": "invoke_agent",
                "startTimeUnixNano": 0, "endTimeUnixNano": 3_000_000,
                "attributes": [
                    {"key": "gen_ai.operation.name", "value": {"stringValue": "invoke_agent"}},
                    {"key": "langgraph.node", "value": {"stringValue": "agent"}},
                    {"key": "session.id", "value": {"stringValue": "agent_evt_sess"}},
                ],
                "events": [{
                    "name": "gen_ai.choice",
                    "attributes": [{"key": "message", "value": {"stringValue": json.dumps({
                        "role": "assistant",
                        "tool_calls": [{
                            "id": "agent_call", "type": "function",
                            "function": {"name": "lookup", "arguments": "{\"q\": \"agent\"}"},
                        }],
                    })}}],
                }],
            }]}]}]
        }
        path = _write_otlp_tempfile(self, doc)
        rows = parse_otel_traces(path, group_by="session.id")
        row = _row(rows, "agent_evt_sess")
        self.assertEqual(row["raw"]["llm_call_count"], 0)
        self.assertEqual(row["raw"]["total_latency_ms"], 0.0)
        self.assertEqual(_tool_events(row)[0]["edit"]["tool_name"], "lookup")

    def test_malformed_event_attributes_do_not_abort_import(self):
        doc = {
            "resourceSpans": [{"scopeSpans": [{"spans": [{
                "traceId": "bad_evt", "spanId": "chat1", "name": "chat",
                "startTimeUnixNano": 0, "endTimeUnixNano": 1_000_000,
                "attributes": [
                    {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
                    {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4o"}},
                    {"key": "gen_ai.output.messages", "value": {"stringValue": "[{\"role\": \"assistant\", \"content\": \"fallback\"}]"}},
                    {"key": "session.id", "value": {"stringValue": "bad_evt_sess"}},
                ],
                "events": [{"name": "gen_ai.choice", "attributes": None}],
            }]}]}]
        }
        path = _write_otlp_tempfile(self, doc)
        rows = parse_otel_traces(path, group_by="session.id")
        targets = _target_events(_row(rows, "bad_evt_sess"))
        self.assertEqual(targets[0]["edit"]["message"]["content"], "fallback")

    def test_standalone_execute_tool_result_does_not_seed_later_duplicate_id(self):
        doc = {
            "resourceSpans": [{"scopeSpans": [{"spans": [
                {
                    "traceId": "stale", "spanId": "tool1", "name": "execute_tool lookup",
                    "startTimeUnixNano": 0, "endTimeUnixNano": 1_000_000,
                    "attributes": [
                        {"key": "gen_ai.operation.name", "value": {"stringValue": "execute_tool"}},
                        {"key": "gen_ai.tool.name", "value": {"stringValue": "lookup"}},
                        {"key": "gen_ai.tool.call.id", "value": {"stringValue": "dup"}},
                        {"key": "gen_ai.tool.call.result", "value": {"stringValue": "first-result"}},
                        {"key": "session.id", "value": {"stringValue": "stale_sess"}},
                    ],
                },
                {
                    "traceId": "stale", "spanId": "chat1", "name": "chat",
                    "startTimeUnixNano": 2_000_000, "endTimeUnixNano": 3_000_000,
                    "attributes": [
                        {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
                        {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4o"}},
                        {"key": "session.id", "value": {"stringValue": "stale_sess"}},
                    ],
                    "events": [{
                        "name": "gen_ai.choice",
                        "attributes": [{"key": "message", "value": {"stringValue": json.dumps({
                            "role": "assistant",
                            "tool_calls": [{
                                "id": "dup", "type": "function",
                                "function": {"name": "lookup", "arguments": "{\"q\": \"later\"}"},
                            }],
                        })}}],
                    }],
                },
            ]}]}]
        }
        path = _write_otlp_tempfile(self, doc)
        rows = parse_otel_traces(path, group_by="session.id")
        tools = _tool_events(_row(rows, "stale_sess"))
        self.assertEqual([t["edit"]["tool_result"] for t in tools], ["first-result", ""])


class TestGenAIDirectExtractionFollowup(unittest.TestCase):
    """Issue #241: direct extraction helpers must not leave pure gen_ai spans blank."""

    def _span(self, *, kind="LLM", span_id="s1", parent=None, name="span", attrs=None, start=0, events=None):
        from assert_ai.core.otel import OTelSpan
        return OTelSpan(
            trace_id="genai_direct",
            span_id=span_id,
            parent_span_id=parent,
            name=name,
            kind=kind,
            start_time_ns=start,
            end_time_ns=start + 1_000_000,
            attributes=attrs or {},
            events=events or [],
        )

    def _genai_llm(self, *, span_id="llm1", parent=None, start=0, text="Answer."):
        return self._span(
            kind="LLM",
            span_id=span_id,
            parent=parent,
            name="chat gpt-4o",
            start=start,
            attrs={
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": "gpt-4o-mini",
                "gen_ai.response.model": "gpt-4o-2024-08-06",
                "gen_ai.usage.input_tokens": 12,
                "gen_ai.usage.output_tokens": 7,
                "gen_ai.input.messages": json.dumps([
                    {"role": "user", "content": "What is the weather?"}
                ]),
                "gen_ai.output.messages": json.dumps([
                    {"role": "assistant", "content": text}
                ]),
                "langgraph.node": "planner",
            },
        )

    def _genai_tool(self, *, span_id="tool1", parent=None, start=0, call_id="call_1"):
        return self._span(
            kind="TOOL",
            span_id=span_id,
            parent=parent,
            name="execute_tool lookup",
            start=start,
            attrs={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "lookup",
                "gen_ai.tool.call.id": call_id,
                "gen_ai.tool.call.arguments": json.dumps({"q": "weather"}),
                "gen_ai.tool.call.result": json.dumps({"result": "sunny"}),
            },
        )

    def test_extract_span_inputs_reads_genai_fields(self):
        from assert_ai.core.otel import extract_span_inputs

        row = extract_span_inputs([self._genai_llm()])[0]
        self.assertEqual(row["query"], "What is the weather?")
        self.assertEqual(row["response"], "Answer.")
        self.assertEqual(row["model"], "gpt-4o-2024-08-06")
        self.assertEqual(row["input_tokens"], 12)
        self.assertEqual(row["output_tokens"], 7)
        self.assertEqual(row["node"], "planner")

    def test_extract_trajectory_inputs_reads_genai_tools_and_tokens(self):
        from assert_ai.core.otel import extract_trajectory_inputs

        llm = self._span(
            kind="LLM",
            span_id="llm1",
            start=0,
            attrs={
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": "gpt-4o",
                "gen_ai.usage.input_tokens": 20,
                "gen_ai.usage.output_tokens": 5,
                "gen_ai.input.messages": json.dumps([
                    {"role": "user", "content": "Find docs"}
                ]),
                "gen_ai.output.messages": json.dumps([{
                    "role": "assistant",
                    "parts": [
                        {"type": "text", "content": "I'll look."},
                        {
                            "type": "tool_call",
                            "id": "call_doc",
                            "name": "search_docs",
                            "arguments": {"query": "GenAI spans"},
                        },
                    ],
                }]),
                "langgraph.node": "researcher",
            },
        )
        tool = self._span(
            kind="TOOL",
            span_id="tool1",
            start=1_000,
            attrs={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "search_docs",
                "gen_ai.tool.call.id": "call_doc",
                "gen_ai.tool.call.arguments": json.dumps({"query": "GenAI spans"}),
                "gen_ai.tool.call.result": "found",
            },
        )
        row = extract_trajectory_inputs([tool, llm])[0]
        self.assertEqual(row["user_input"], "Find docs")
        self.assertEqual(row["total_tokens"], {"input": 20, "output": 5})
        self.assertEqual(json.loads(row["node_path"]), ["researcher"])
        self.assertEqual(
            json.loads(row["tool_calls"]),
            [{"name": "search_docs", "arguments": {"query": "GenAI spans"}}],
        )

    def test_extract_session_inputs_reads_genai_messages_and_tool_calls(self):
        from assert_ai.core.otel import extract_session_inputs

        row = extract_session_inputs([
            self._genai_llm(span_id="llm1"),
            self._genai_tool(span_id="tool1", start=1_000),
        ])[0]
        self.assertEqual(json.loads(row["user_inputs"]), ["What is the weather?"])
        self.assertEqual(json.loads(row["output_messages"]), ["Answer."])
        self.assertEqual(json.loads(row["tool_calls"]), ['lookup({"q": "weather"})'])

    def test_span_node_to_dict_reads_genai_fields(self):
        from assert_ai.core.otel import SpanNode

        llm_dict = SpanNode(self._genai_llm()).to_dict(include_input=True)
        self.assertEqual(llm_dict["model"], "gpt-4o-2024-08-06")
        self.assertEqual(llm_dict["tokens"], {"input": 12, "output": 7})
        self.assertEqual(llm_dict["input"], "What is the weather?")
        self.assertEqual(llm_dict["output"], "Answer.")

        tool_dict = SpanNode(self._genai_tool()).to_dict(include_input=True)
        self.assertEqual(tool_dict["tool_name"], "lookup")
        self.assertEqual(tool_dict["tool_args"], {"q": "weather"})
        self.assertIn("sunny", tool_dict["tool_result"])

    def test_tree_mode_auto_selection_preserves_pure_genai_content(self):
        from assert_ai.core.otel import extract_for_judge, ExtractionMode

        event_carried_tool_call = {
            "name": "gen_ai.choice",
            "attributes": [{"key": "message", "value": {"stringValue": json.dumps({
                "role": "assistant",
                "content": "Need a lookup.",
                "tool_calls": [{
                    "id": "event_call",
                    "type": "function",
                    "function": {
                        "name": "search_docs",
                        "arguments": json.dumps({"query": "GenAI spans"}),
                    },
                }],
            })}}],
        }
        event_carried_tool_result = {
            "name": "gen_ai.tool.message",
            "attributes": [
                {"key": "id", "value": {"stringValue": "event_call"}},
                {"key": "content", "value": {"stringValue": "docs found"}},
            ],
        }
        spans = [self._span(
            kind="AGENT",
            span_id="root",
            name="invoke_agent",
            attrs={"gen_ai.operation.name": "invoke_agent", "langgraph.node": "agent"},
        )]
        spans.append(self._genai_llm(
            span_id="llm0",
            parent="root",
            start=1_000,
            text="Answer 0.",
        ))
        spans.append(self._span(
            kind="LLM",
            span_id="llm_event_tool",
            parent="root",
            name="chat gpt-4o",
            start=1_500,
            attrs={
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": "gpt-4o",
                "langgraph.node": "tool_requester",
            },
            events=[event_carried_tool_call, event_carried_tool_result],
        ))
        spans.append(self._genai_tool(span_id="tool0", parent="root", start=2_000))
        for i in range(7):
            spans.append(self._genai_llm(
                span_id=f"llm_extra_{i}",
                parent="root",
                start=3_000 + i,
                text=f"Extra answer {i}.",
            ))

        result = extract_for_judge(spans)
        self.assertEqual(result["mode"], ExtractionMode.TREE)
        root = result["representation"][0]
        llm_child = next(c for c in root["children"] if c["span_id"] == "llm0")
        tool_requester = next(c for c in root["children"] if c["span_id"] == "llm_event_tool")
        tool_child = next(c for c in root["children"] if c["span_id"] == "tool0")
        self.assertEqual(llm_child["model"], "gpt-4o-2024-08-06")
        self.assertEqual(llm_child["output"], "Answer 0.")
        self.assertEqual(tool_requester["tool_calls"], [{
            "name": "search_docs",
            "arguments": {"query": "GenAI spans"},
            "tool_call_id": "event_call",
            "tool_result": "docs found",
        }])
        self.assertEqual(tool_child["tool_name"], "lookup")
        self.assertEqual(tool_child["tool_args"], {"q": "weather"})
        self.assertIn("sunny", tool_child["tool_result"])

    def test_repeated_call_ids_are_not_collapsed_in_direct_helpers(self):
        from assert_ai.core.otel import extract_trajectory_inputs, extract_session_inputs

        def choice_span(span_id, q, start):
            return self._span(
                kind="LLM",
                span_id=span_id,
                start=start,
                attrs={
                    "gen_ai.operation.name": "chat",
                    "gen_ai.request.model": "gpt-4o",
                    "gen_ai.input.messages": json.dumps([
                        {"role": "user", "content": f"lookup {q}"}
                    ]),
                    "gen_ai.output.messages": json.dumps([{
                        "role": "assistant",
                        "tool_calls": [{
                            "id": "dup",
                            "type": "function",
                            "function": {
                                "name": "lookup",
                                "arguments": json.dumps({"q": q}),
                            },
                        }],
                    }]),
                    "session.id": "sess_dup",
                },
            )

        spans = [choice_span("llm1", "first", 0), choice_span("llm2", "second", 1_000)]
        traj_calls = json.loads(extract_trajectory_inputs(spans)[0]["tool_calls"])
        self.assertEqual(
            traj_calls,
            [
                {"name": "lookup", "arguments": {"q": "first"}},
                {"name": "lookup", "arguments": {"q": "second"}},
            ],
        )
        session_calls = json.loads(extract_session_inputs(spans)[0]["tool_calls"])
        self.assertEqual(session_calls, ['lookup({"q": "first"})', 'lookup({"q": "second"})'])

    def test_tool_arg_raw_value_still_truncates_in_tree_serialization(self):
        from assert_ai.core.otel import OTelSpan, SpanNode

        span = OTelSpan(
            trace_id="t1",
            span_id="tool_raw",
            parent_span_id=None,
            name="tool",
            kind="TOOL",
            start_time_ns=0,
            end_time_ns=1_000_000,
            attributes={
                "openinference.span.kind": "TOOL",
                "tool.name": "bad_args_tool",
                "input.value": "x" * 2000,
                "output.value": "ok",
            },
        )
        d = SpanNode(span).to_dict(max_content_chars=50)
        self.assertEqual(d["tool_args"], {"raw": "x" * 50})

    def test_dual_emitting_spans_keep_openinference_precedence(self):
        from assert_ai.core.otel import extract_span_inputs, SpanNode

        span = self._span(
            kind="LLM",
            attrs={
                "openinference.span.kind": "LLM",
                "input.value": "openinference input",
                "output.value": "openinference output",
                "llm.model_name": "oi-model",
                "llm.token_count.prompt": 3,
                "llm.token_count.completion": 4,
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": "genai-model",
                "gen_ai.input.messages": json.dumps([
                    {"role": "user", "content": "genai input"}
                ]),
                "gen_ai.output.messages": json.dumps([
                    {"role": "assistant", "content": "genai output"}
                ]),
            },
        )

        row = extract_span_inputs([span])[0]
        self.assertEqual(row["query"], "openinference input")
        self.assertEqual(row["response"], "openinference output")
        self.assertEqual(row["model"], "oi-model")
        self.assertEqual(SpanNode(span).to_dict()["output"], "openinference output")


if __name__ == "__main__":
    unittest.main()
