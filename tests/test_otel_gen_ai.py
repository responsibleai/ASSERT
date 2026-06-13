# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for OTel GenAI semantic-convention (``gen_ai.*``) span parsing.

These cover the newer OpenTelemetry GenAI semconv that a growing number of
agent runtimes emit, in addition to the existing OpenInference conventions.
The parser must map ``gen_ai.*`` spans into the same ASSERT inference-row
shape (``{metadata, events, raw}`` with ``tool_call`` / ``add_message``
events) without regressing the OpenInference path.

Spec references (authoritative, not memory):
- https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/
- gen-ai-events (gen_ai.choice / gen_ai.assistant.message / gen_ai.tool.message)

The ``gen_ai.*`` headline must stand on generic semconv alone; the
``openclaw.content.*`` vendor attributes are an optional enrichment layer
for content fidelity, never a requirement.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from assert_ai.core.otel import parse_otel_traces

FIXTURES = Path(__file__).parent / "fixtures"
GENAI_TRACES = FIXTURES / "gen_ai_otel_traces.json"
GENAI_OPENCLAW_TRACES = FIXTURES / "gen_ai_openclaw_enrichment_traces.json"
OPENINFERENCE_TRACES = FIXTURES / "sample_otel_traces.json"


def _write_temp_otlp(payload: dict) -> Path:
    """Dump an OTLP-JSON payload to a temp file and return its path."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    )
    json.dump(payload, tmp)
    tmp.flush()
    tmp.close()
    return Path(tmp.name)


# ── Headline: generic gen_ai.* spans (no vendor enrichment) ──────────


class TestGenAiParser(unittest.TestCase):
    """gen_ai.* spans map into ASSERT event rows on generic semconv alone."""

    def setUp(self):
        self.rows = parse_otel_traces(GENAI_TRACES, group_by="session.id")

    def test_groups_into_single_session(self):
        self.assertEqual(len(self.rows), 1)
        self.assertEqual(self.rows[0]["metadata"]["session_id"], "sess_genai_trip")

    def test_row_schema_unchanged(self):
        row = self.rows[0]
        self.assertIn("metadata", row)
        self.assertIn("events", row)
        self.assertIn("raw", row)
        self.assertEqual(row["metadata"]["type"], "otel_import")
        self.assertEqual(row["metadata"]["runtime_mode"], "otel_traced")

    def test_event_counts(self):
        """Two chat spans -> 2 assistant messages; one execute_tool -> 1 tool call."""
        events = self.rows[0]["events"]
        target_events = [e for e in events if e["actor"] == "target"]
        tool_events = [e for e in events if e["actor"] == "tool"]
        self.assertEqual(len(target_events), 2)
        self.assertEqual(len(tool_events), 1)

    def test_event_carried_choice_content(self):
        """First chat span's assistant text comes from a gen_ai.choice span EVENT."""
        target_events = [e for e in self.rows[0]["events"] if e["actor"] == "target"]
        first = target_events[0]
        self.assertEqual(first["edit"]["type"], "add_message")
        self.assertEqual(first["edit"]["message"]["role"], "assistant")
        self.assertEqual(
            first["edit"]["message"]["content"],
            '{"intent": "book_trip", "destination": "Tokyo"}',
        )

    def test_attribute_carried_output_messages_content(self):
        """Second chat span's text comes from the gen_ai.output.messages ATTRIBUTE."""
        target_events = [e for e in self.rows[0]["events"] if e["actor"] == "target"]
        second = target_events[1]
        self.assertIn("optimized Tokyo itinerary", second["edit"]["message"]["content"])

    def test_llm_metadata_model_and_tokens(self):
        target_events = [e for e in self.rows[0]["events"] if e["actor"] == "target"]
        first = target_events[0]
        # response.model is preferred over request.model.
        self.assertEqual(first["raw"]["_model"], "gpt-4o-2024-08-06")
        self.assertEqual(first["raw"]["_tokens"]["input"], 85)
        self.assertEqual(first["raw"]["_tokens"]["output"], 42)

    def test_tool_call_mapping(self):
        tool_events = [e for e in self.rows[0]["events"] if e["actor"] == "tool"]
        tool = tool_events[0]
        self.assertEqual(tool["edit"]["type"], "tool_call")
        self.assertEqual(tool["edit"]["tool_name"], "search_flights")
        self.assertEqual(tool["edit"]["tool_args"]["destination"], "NRT")
        self.assertEqual(tool["edit"]["tool_args"]["max_price"], 1500)
        self.assertIn("ANA", tool["edit"]["tool_result"])

    def test_tool_call_id_preserved(self):
        """gen_ai.tool.call.id is carried on the tool_call edit (viewer reads it)."""
        tool_events = [e for e in self.rows[0]["events"] if e["actor"] == "tool"]
        self.assertEqual(tool_events[0]["edit"]["tool_call_id"], "call_flights_001")

    def test_aggregate_metadata(self):
        agg = self.rows[0]["raw"]
        self.assertEqual(agg["llm_call_count"], 2)
        self.assertEqual(agg["total_tokens"]["input"], 85 + 210)
        self.assertEqual(agg["total_tokens"]["output"], 42 + 95)
        self.assertIn("search_flights", agg["tools_called"])


# ── Optional openclaw.* enrichment layer (content fidelity add-on) ───


class TestGenAiOpenClawEnrichment(unittest.TestCase):
    """openclaw.content.* enriches content fidelity but is never required."""

    def setUp(self):
        self.rows = parse_otel_traces(GENAI_OPENCLAW_TRACES, group_by="session.id")

    def test_enrichment_output_messages_win_over_generic(self):
        target_events = [e for e in self.rows[0]["events"] if e["actor"] == "target"]
        content = target_events[0]["edit"]["message"]["content"]
        self.assertIn("OPENCLAW full fidelity", content)
        self.assertNotIn("GENERIC short answer", content)

    def test_enrichment_tool_input_output_win_over_generic(self):
        tool_events = [e for e in self.rows[0]["events"] if e["actor"] == "tool"]
        tool = tool_events[0]
        # Generic gen_ai.tool.call.arguments only had {"city": "Tokyo"};
        # the openclaw enrichment carries the full argument object.
        self.assertEqual(tool["edit"]["tool_args"]["max_price_per_night"], 250)
        self.assertIn("Granbell", tool["edit"]["tool_result"])
        self.assertNotIn("[truncated]", tool["edit"]["tool_result"])


# ── Graceful degradation when optional gen_ai fields are absent ──────


class TestGenAiGracefulDegradation(unittest.TestCase):
    """Missing optional gen_ai fields must not crash or drop the span."""

    def test_chat_span_without_content_or_usage(self):
        payload = {
            "resourceSpans": [{"scopeSpans": [{"spans": [{
                "traceId": "t_min",
                "spanId": "s_min",
                "name": "chat gpt-4o",
                "startTimeUnixNano": 1000000000,
                "endTimeUnixNano": 1100000000,
                "attributes": [
                    {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
                    {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4o"}},
                    {"key": "session.id", "value": {"stringValue": "sess_min"}},
                ],
            }]}]}]
        }
        path = _write_temp_otlp(payload)
        rows = parse_otel_traces(path, group_by="session.id")
        self.assertEqual(len(rows), 1)
        # Counted as an LLM call even though it emitted no message content.
        self.assertEqual(rows[0]["raw"]["llm_call_count"], 1)

    def test_execute_tool_span_with_only_name(self):
        payload = {
            "resourceSpans": [{"scopeSpans": [{"spans": [{
                "traceId": "t_tool",
                "spanId": "s_tool",
                "name": "execute_tool lookup",
                "startTimeUnixNano": 1000000000,
                "endTimeUnixNano": 1100000000,
                "attributes": [
                    {"key": "gen_ai.operation.name", "value": {"stringValue": "execute_tool"}},
                    {"key": "gen_ai.tool.name", "value": {"stringValue": "lookup"}},
                    {"key": "session.id", "value": {"stringValue": "sess_tool"}},
                ],
            }]}]}]
        }
        path = _write_temp_otlp(payload)
        rows = parse_otel_traces(path, group_by="session.id")
        tool_events = [e for e in rows[0]["events"] if e["actor"] == "tool"]
        self.assertEqual(len(tool_events), 1)
        self.assertEqual(tool_events[0]["edit"]["tool_name"], "lookup")
        self.assertEqual(tool_events[0]["edit"]["tool_args"], {})
        self.assertEqual(tool_events[0]["edit"]["tool_result"], "")
        self.assertIsNone(tool_events[0]["edit"]["tool_call_id"])


# ── Span validation treats gen_ai spans as first-class ──────────────


class TestGenAiSpanValidation(unittest.TestCase):
    """validate_spans must not flag gen_ai spans as missing the OpenInference kind."""

    def test_gen_ai_span_not_flagged_unknown(self):
        from assert_ai.core.otel import _parse_otlp_json, validate_spans

        spans = _parse_otlp_json(GENAI_TRACES)
        result = validate_spans(spans)
        self.assertFalse(
            any("openinference.span.kind" in w for w in result.warnings),
            f"gen_ai spans should not warn about OpenInference kind: {result.warnings}",
        )


# ── Coexistence: OpenInference path must remain unchanged ────────────


class TestOpenInferenceStillParses(unittest.TestCase):
    """Adding gen_ai support must not regress OpenInference parsing."""

    def test_openinference_sample_unchanged(self):
        rows = parse_otel_traces(OPENINFERENCE_TRACES, group_by="session.id")
        session_ids = {r["metadata"]["session_id"] for r in rows}
        self.assertEqual(session_ids, {"sess_tokyo_trip", "sess_paris_trip"})
        tokyo = next(r for r in rows if r["metadata"]["session_id"] == "sess_tokyo_trip")
        target_events = [e for e in tokyo["events"] if e["actor"] == "target"]
        tool_events = [e for e in tokyo["events"] if e["actor"] == "tool"]
        self.assertEqual(len(target_events), 3)
        self.assertEqual(len(tool_events), 2)


if __name__ == "__main__":
    unittest.main()
