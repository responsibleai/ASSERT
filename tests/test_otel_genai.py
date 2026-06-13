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


if __name__ == "__main__":
    unittest.main()
