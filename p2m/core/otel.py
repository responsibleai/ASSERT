# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""OTel trace parser — converts OTLP JSON to p2m transcript events.

Supports traces exported from Phoenix, Jaeger, or any OpenTelemetry collector.
Follows OpenInference semantic conventions for LLM/agent span attributes.

Usage:
    from p2m.core.otel import parse_otel_traces

    inference_rows = parse_otel_traces(
        "traces.json",
        group_by="session.id",
    )
    # Returns list[dict] in p2m inference-row format (same as inference_set.jsonl)
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


# OpenInference semantic conventions
# https://arize-ai.github.io/openinference/

_SPAN_KIND_KEY = "openinference.span.kind"
_INPUT_VALUE_KEY = "input.value"
_OUTPUT_VALUE_KEY = "output.value"
_LLM_MODEL_KEY = "llm.model_name"
_LLM_INPUT_TOKENS_KEY = "llm.token_count.prompt"
_LLM_OUTPUT_TOKENS_KEY = "llm.token_count.completion"
_TOOL_NAME_KEY = "tool.name"
_LANGGRAPH_NODE_KEY = "langgraph.node"


@dataclass
class OTelSpan:
    """Minimal representation of an OpenTelemetry span."""
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    kind: str
    start_time_ns: int
    end_time_ns: int
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "OK"

    @property
    def latency_ms(self) -> float:
        return (self.end_time_ns - self.start_time_ns) / 1_000_000


def parse_otel_traces(
    path: str | Path,
    *,
    group_by: str = "session.id",
) -> list[dict[str, Any]]:
    """Parse OTLP JSON export into p2m inference rows.

    Args:
        path: Path to OTLP JSON file.
        group_by: Span attribute key to group spans into conversations.

    Returns:
        List of inference row dicts, one per conversation. Each row has the
        same schema as a row in inference_set.jsonl:
        {
            "metadata": {...},
            "events": [...],
            "raw": {...},
        }
    """
    spans = _parse_otlp_json(Path(path))
    grouped = _group_spans(spans, group_by)

    rows = []
    for session_id, session_spans in grouped.items():
        session_spans.sort(key=lambda s: s.start_time_ns)
        events, aggregate = _spans_to_events(session_spans)

        rows.append({
            "metadata": {
                "type": "otel_import",
                "session_id": session_id,
                "runtime_mode": "otel_traced",
            },
            "events": events,
            "raw": aggregate,
        })

    return rows


def _parse_otlp_json(path: Path) -> list[OTelSpan]:
    """Parse OTLP JSON export format."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"OTLP trace file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed JSON in OTLP trace file {path}: {exc}") from exc

    spans: list[OTelSpan] = []
    for resource_span in data.get("resourceSpans", []):
        for scope_span in resource_span.get("scopeSpans", []):
            for raw in scope_span.get("spans", []):
                attrs = _flatten_attributes(raw.get("attributes", []))
                spans.append(OTelSpan(
                    trace_id=raw.get("traceId", ""),
                    span_id=raw.get("spanId", ""),
                    parent_span_id=raw.get("parentSpanId"),
                    name=raw.get("name", ""),
                    kind=attrs.get(_SPAN_KIND_KEY, "UNKNOWN"),
                    start_time_ns=int(raw.get("startTimeUnixNano", 0)),
                    end_time_ns=int(raw.get("endTimeUnixNano", 0)),
                    attributes=attrs,
                    status=raw.get("status", {}).get("code", "OK"),
                ))
    return spans


def _flatten_attributes(attrs: list[dict]) -> dict[str, Any]:
    """Convert OTLP attribute array [{key, value}] to flat dict."""
    result: dict[str, Any] = {}
    for attr in attrs:
        key = attr.get("key", "")
        value = attr.get("value", {})
        if "stringValue" in value:
            result[key] = value["stringValue"]
        elif "intValue" in value:
            result[key] = int(value["intValue"])
        elif "doubleValue" in value:
            result[key] = float(value["doubleValue"])
        elif "boolValue" in value:
            result[key] = value["boolValue"]
        elif "arrayValue" in value:
            result[key] = [
                _extract_value(v) for v in value["arrayValue"].get("values", [])
            ]
    return result


def _extract_value(value_obj: dict) -> Any:
    """Extract a scalar value from an OTLP Value object."""
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if key in value_obj:
            return value_obj[key]
    return None


def _group_spans(
    spans: list[OTelSpan],
    group_key: str,
) -> dict[str, list[OTelSpan]]:
    """Group spans by a session/conversation attribute."""
    groups: dict[str, list[OTelSpan]] = {}
    for span in spans:
        session_id = str(span.attributes.get(group_key, span.trace_id))
        groups.setdefault(session_id, []).append(span)
    return groups


def _spans_to_events(
    spans: list[OTelSpan],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Convert a group of spans into p2m transcript events + aggregate metadata.

    Returns:
        (events, aggregate) where events is a list of transcript event dicts
        and aggregate is summary metadata for the conversation.
    """
    events: list[dict[str, Any]] = []
    nodes_visited: list[str] = []
    tools_called: list[str] = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_latency_ms = 0.0
    llm_call_count = 0

    for span in spans:
        if span.kind == "LLM":
            output_text = span.attributes.get(_OUTPUT_VALUE_KEY, "")
            model = span.attributes.get(_LLM_MODEL_KEY, "")
            input_tokens = span.attributes.get(_LLM_INPUT_TOKENS_KEY, 0)
            output_tokens = span.attributes.get(_LLM_OUTPUT_TOKENS_KEY, 0)
            node_name = span.attributes.get(_LANGGRAPH_NODE_KEY, span.name)

            total_input_tokens += input_tokens
            total_output_tokens += output_tokens
            total_latency_ms += span.latency_ms
            llm_call_count += 1

            if node_name and node_name not in nodes_visited:
                nodes_visited.append(node_name)

            if output_text:
                events.append({
                    "view": ["target", "combined"],
                    "actor": "target",
                    "edit": {
                        "type": "add_message",
                        "message": {"role": "assistant", "content": output_text},
                    },
                    "raw": {
                        "_node": node_name,
                        "_model": model,
                        "_tokens": {"input": input_tokens, "output": output_tokens},
                        "_latency_ms": span.latency_ms,
                    },
                })

        elif span.kind == "TOOL":
            tool_name = span.attributes.get(_TOOL_NAME_KEY, span.name)
            tool_input = span.attributes.get(_INPUT_VALUE_KEY, "")
            tool_output = span.attributes.get(_OUTPUT_VALUE_KEY, "")

            if tool_name and tool_name not in tools_called:
                tools_called.append(tool_name)

            try:
                tool_args = json.loads(tool_input) if tool_input else {}
            except (json.JSONDecodeError, TypeError):
                tool_args = {"raw": tool_input}

            events.append({
                "view": ["target", "combined"],
                "actor": "tool",
                "edit": {
                    "type": "tool_call",
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "tool_result": tool_output or "",
                },
            })

        elif span.kind == "CHAIN":
            node_name = span.attributes.get(_LANGGRAPH_NODE_KEY, span.name)
            if node_name and node_name not in nodes_visited:
                nodes_visited.append(node_name)

        else:
            # UNKNOWN or unrecognized span kind — include with available attributes
            node_name = span.attributes.get(_LANGGRAPH_NODE_KEY, span.name)
            if node_name and node_name not in nodes_visited:
                nodes_visited.append(node_name)
            input_text = span.attributes.get(_INPUT_VALUE_KEY, "")
            output_text = span.attributes.get(_OUTPUT_VALUE_KEY, "")
            if input_text or output_text:
                events.append({
                    "view": ["target", "combined"],
                    "actor": "target",
                    "edit": {
                        "type": "add_message",
                        "message": {
                            "role": "assistant",
                            "content": output_text or f"[{span.kind or 'unknown'}] {span.name}",
                        },
                    },
                    "raw": {
                        "_node": node_name,
                        "_span_kind": span.kind,
                        "_latency_ms": span.latency_ms,
                    },
                })

    aggregate = {
        "nodes_visited": nodes_visited,
        "tools_called": tools_called,
        "total_tokens": {
            "input": total_input_tokens,
            "output": total_output_tokens,
        },
        "total_latency_ms": total_latency_ms,
        "llm_call_count": llm_call_count,
    }

    return events, aggregate


# ── Span validation ───────────────────────────────────────────────


@dataclass
class SpanValidationResult:
    """Result of validating spans for eval readiness."""

    valid: bool
    missing_attributes: list[str]
    warnings: list[str]


def validate_spans(spans: list[OTelSpan]) -> SpanValidationResult:
    """Check spans for OpenInference attributes. Warns but never drops.

    All spans pass through to the judge regardless of validation result.
    The ``valid`` flag is informational — ``True`` means all recommended
    attributes are present, ``False`` means some are missing but the spans
    are still usable.
    """
    warnings: list[str] = []

    for span in spans:
        if span.kind == "UNKNOWN":
            warnings.append(f"span {span.span_id}: missing {_SPAN_KIND_KEY}")

        if span.kind == "LLM":
            if not span.attributes.get(_OUTPUT_VALUE_KEY):
                warnings.append(f"span {span.span_id}: missing {_OUTPUT_VALUE_KEY}")
            if not span.attributes.get(_LLM_MODEL_KEY):
                warnings.append(f"span {span.span_id}: missing {_LLM_MODEL_KEY}")
            if (
                not span.attributes.get(_LLM_INPUT_TOKENS_KEY)
                and not span.attributes.get(_LLM_OUTPUT_TOKENS_KEY)
            ):
                warnings.append(f"span {span.span_id}: missing token counts")

        if span.kind == "TOOL":
            if not span.attributes.get(_TOOL_NAME_KEY):
                warnings.append(f"span {span.span_id}: missing {_TOOL_NAME_KEY}")

    return SpanValidationResult(
        valid=len(warnings) == 0,
        missing_attributes=[],
        warnings=warnings,
    )


# ── Trace compression ────────────────────────────────────────────


def compress_trace_for_judge(
    events: list[dict[str, Any]],
    *,
    max_events: int = 50,
    include_tool_args: bool = True,
    include_token_counts: bool = True,
) -> list[dict[str, Any]]:
    """Compress a trace to fit within judge token budget.

    Strategy: Keep all tool call events (they're evidence), keep first and
    last LLM events per node, drop intermediate LLM events if over budget.
    """
    if len(events) <= max_events:
        result = events
    else:
        # Partition: tool events are always kept; LLM events may be trimmed
        tool_events = [e for e in events if e.get("actor") == "tool"]
        llm_events = [e for e in events if e.get("actor") != "tool"]

        remaining = max_events - len(tool_events)
        if remaining < 0:
            remaining = 0

        if remaining >= len(llm_events):
            trimmed_llm = llm_events
        else:
            # Keep first and last LLM event per node, drop middle ones
            by_node: dict[str, list[dict[str, Any]]] = {}
            for ev in llm_events:
                node = (ev.get("raw") or {}).get("_node", "_default")
                by_node.setdefault(node, []).append(ev)

            trimmed_llm = []
            for node_events in by_node.values():
                if len(node_events) <= 2:
                    trimmed_llm.extend(node_events)
                else:
                    trimmed_llm.append(node_events[0])
                    trimmed_llm.append(node_events[-1])

            # If still over budget, hard-truncate
            if len(trimmed_llm) > remaining:
                trimmed_llm = trimmed_llm[:remaining]

        result = tool_events + trimmed_llm

    # Optionally strip tool args or token counts to save tokens
    if not include_tool_args or not include_token_counts:
        compressed = []
        for event in result:
            event = dict(event)
            if not include_tool_args and event.get("actor") == "tool":
                edit = dict(event.get("edit", {}))
                edit.pop("tool_args", None)
                event["edit"] = edit
            if not include_token_counts and "raw" in event:
                raw = dict(event["raw"])
                raw.pop("_tokens", None)
                event["raw"] = raw
            compressed.append(event)
        return compressed

    return result


# ── Trace exporters ──────────────────────────────────────────────


@runtime_checkable
class TraceExporter(Protocol):
    """Interface for exporting OTel traces. Decouples from Phoenix."""

    def export_session(self, session_id: str) -> list[OTelSpan]: ...


class FileTraceExporter:
    """Reads OTLP JSON from file. No external dependencies."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def export_session(self, session_id: str) -> list[OTelSpan]:
        """Return spans whose ``session.id`` attribute matches *session_id*.

        Falls back to matching on ``trace_id`` when no ``session.id``
        attribute is present.
        """
        all_spans = _parse_otlp_json(self._path)
        return [
            s for s in all_spans
            if s.attributes.get("session.id", s.trace_id) == session_id
        ]


class InMemoryTraceExporter:
    """Collects spans in-memory during a inference. For testing and CI."""

    def __init__(self) -> None:
        self._spans: list[OTelSpan] = []

    def add_span(self, span: OTelSpan) -> None:
        self._spans.append(span)

    def export_session(self, session_id: str) -> list[OTelSpan]:
        """Return spans whose ``session.id`` attribute or trace_id matches."""
        return [
            s for s in self._spans
            if s.attributes.get("session.id", s.trace_id) == session_id
        ]


class LiveOTelExporter:
    """In-process span collector that piggybacks on the global TracerProvider.

    Works with any TracerProvider — whether set by Phoenix's ``register()``,
    manual OTel setup, or created by us as a fallback. Captures all spans
    emitted during each turn via a custom SpanProcessor.

    Process-level singleton: setup() only runs once. Each OTelTracedSession
    calls clear() before its turn and export_session() after.
    """

    _instance: "LiveOTelExporter | None" = None
    _setup_done: bool = False
    _sdk_exporter: Any = None
    # Per-event-loop async lock, created lazily on first use. A class-level
    # ``asyncio.Lock()`` would bind to whichever loop happened to be running
    # at first use and then raise in any subsequent ``asyncio.run()``. We
    # cache (loop, lock) and recreate when the loop changes so:
    #   - within one inference (one event loop), all concurrent sessions share
    #     the same lock and serialize the clear-invoke-export cycle;
    #   - tests / repeated runs that create new event loops still work.
    # NOTE: ``threading.Lock`` MUST NOT be used here — it would block the
    # entire event loop across the inner ``await`` and deadlock when
    # ``inference.concurrency > 1``.
    _lock: asyncio.Lock | None = None
    _lock_loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def get_lock(cls) -> asyncio.Lock:
        """Return an ``asyncio.Lock`` bound to the current running loop."""
        loop = asyncio.get_running_loop()
        if cls._lock is None or cls._lock_loop is not loop:
            cls._lock = asyncio.Lock()
            cls._lock_loop = loop
        return cls._lock

    def __new__(cls) -> "LiveOTelExporter":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def setup(self) -> None:
        """Attach our collector to the global TracerProvider.

        If Phoenix (or anything else) already set a TracerProvider, we add
        our SpanProcessor to it — no conflict. If no provider exists, we
        create a minimal one as fallback.
        """
        if LiveOTelExporter._setup_done:
            return

        from opentelemetry import trace as otel_trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            SimpleSpanProcessor,
            SpanExporter,
            SpanExportResult,
        )

        class _Collector(SpanExporter):
            """In-process span sink — accumulates spans for per-turn retrieval."""
            def __init__(self):
                self.spans: list = []
            def export(self, spans):
                self.spans.extend(spans)
                return SpanExportResult.SUCCESS
            def shutdown(self):
                pass

        LiveOTelExporter._sdk_exporter = _Collector()
        processor = SimpleSpanProcessor(LiveOTelExporter._sdk_exporter)

        def _add_processor_preserving(provider, proc):
            # Phoenix's TracerProvider removes its default gRPC exporter
            # when add_span_processor is called. Passing
            # replace_default_processor=False preserves it. Standard OTel
            # SDK providers don't accept this kwarg, so fall back to the
            # normal call which already appends correctly.
            try:
                provider.add_span_processor(proc, replace_default_processor=False)
            except TypeError:
                provider.add_span_processor(proc)

        # Piggyback on existing provider if one is set (e.g., by Phoenix register())
        existing = otel_trace.get_tracer_provider()
        if isinstance(existing, TracerProvider):
            _add_processor_preserving(existing, processor)
        else:
            # Unwrap ProxyTracerProvider if needed
            real = getattr(existing, "_real_provider", None)
            if isinstance(real, TracerProvider):
                _add_processor_preserving(real, processor)
            else:
                # No SDK provider exists — create one as fallback
                provider = TracerProvider()
                provider.add_span_processor(processor)
                otel_trace.set_tracer_provider(provider)

        LiveOTelExporter._setup_done = True

    def clear(self) -> None:
        """Clear captured spans (call between turns)."""
        if LiveOTelExporter._sdk_exporter is not None:
            LiveOTelExporter._sdk_exporter.spans.clear()

    def export_session(self, session_id: str) -> list[OTelSpan]:
        """Convert all captured OTel SDK spans to p2m OTelSpan format.

        Since clear() is called between turns, all spans belong to the
        current turn. The session_id parameter is kept for interface compat
        but filtering is not needed.
        """
        if LiveOTelExporter._sdk_exporter is None:
            return []
        result: list[OTelSpan] = []
        for sdk_span in list(LiveOTelExporter._sdk_exporter.spans):
            attrs = {}
            for k, v in (sdk_span.attributes or {}).items():
                attrs[k] = v
            result.append(OTelSpan(
                trace_id=f"{sdk_span.context.trace_id:032x}" if sdk_span.context else "",
                span_id=f"{sdk_span.context.span_id:016x}" if sdk_span.context else "",
                parent_span_id=(
                    f"{sdk_span.parent.span_id:016x}"
                    if sdk_span.parent else None
                ),
                name=sdk_span.name,
                kind=attrs.get("openinference.span.kind", "UNKNOWN"),
                start_time_ns=sdk_span.start_time or 0,
                end_time_ns=sdk_span.end_time or 0,
                attributes=attrs,
            ))
        return result


# ── Extraction APIs (3 granularities) ─────────────────────────────
# These productize Arize's notebook utility functions as typed, tested APIs.


def extract_span_inputs(
    spans: list[OTelSpan],
    *,
    span_kind: str = "LLM",
) -> list[dict[str, Any]]:
    """Extract eval inputs from individual spans.

    Returns one dict per matching span with:
    - query: the input to this span
    - response: the output from this span
    - model: the LLM model used (if available)
    - tokens: input/output token counts (if available)
    """
    results = []
    for span in spans:
        if span.kind != span_kind:
            continue
        results.append({
            "span_id": span.span_id,
            "trace_id": span.trace_id,
            "query": span.attributes.get(_INPUT_VALUE_KEY, ""),
            "response": span.attributes.get(_OUTPUT_VALUE_KEY, ""),
            "model": span.attributes.get(_LLM_MODEL_KEY, ""),
            "input_tokens": span.attributes.get(_LLM_INPUT_TOKENS_KEY, 0),
            "output_tokens": span.attributes.get(_LLM_OUTPUT_TOKENS_KEY, 0),
            "latency_ms": span.latency_ms,
            "node": span.attributes.get(_LANGGRAPH_NODE_KEY, span.name),
        })
    return results


def extract_trajectory_inputs(
    spans: list[OTelSpan],
    *,
    group_by: str = "trace_id",
) -> list[dict[str, Any]]:
    """Extract trajectory eval inputs — one row per trace.

    Groups spans by trace, extracts tool calls in order, collapses
    to the format needed for trajectory evaluation prompts.

    Returns one dict per trace with:
    - trace_id: the trace identifier
    - user_input: the first user input in the trace
    - tool_calls: ordered list of {name, arguments} dicts
    - tool_defs: tool definitions (if available)
    - node_path: ordered list of node names visited
    - total_tokens: aggregate token usage
    """
    if group_by == "trace_id":
        grouped = _group_spans_by_trace(spans)
    else:
        grouped = _group_spans(spans, group_by)

    results = []
    for group_id, group_spans in grouped.items():
        group_spans.sort(key=lambda s: s.start_time_ns)

        user_input = ""
        tool_calls: list[dict[str, Any]] = []
        tool_defs: list[Any] = []
        node_path: list[str] = []
        total_input_tokens = 0
        total_output_tokens = 0

        for span in group_spans:
            if span.kind == "LLM":
                if not user_input:
                    user_input = span.attributes.get(_INPUT_VALUE_KEY, "")
                node = span.attributes.get(_LANGGRAPH_NODE_KEY, span.name)
                if node and node not in node_path:
                    node_path.append(node)
                total_input_tokens += span.attributes.get(_LLM_INPUT_TOKENS_KEY, 0)
                total_output_tokens += span.attributes.get(_LLM_OUTPUT_TOKENS_KEY, 0)
            elif span.kind == "TOOL":
                tool_name = span.attributes.get(_TOOL_NAME_KEY, span.name)
                tool_input = span.attributes.get(_INPUT_VALUE_KEY, "")
                try:
                    args = json.loads(tool_input) if tool_input else {}
                except (json.JSONDecodeError, TypeError):
                    args = {"raw": tool_input}
                tool_calls.append({"name": tool_name, "arguments": args})

        results.append({
            "trace_id": group_id,
            "user_input": user_input,
            "tool_calls": json.dumps(tool_calls),
            "tool_defs": json.dumps(tool_defs),
            "node_path": json.dumps(node_path),
            "total_tokens": {"input": total_input_tokens, "output": total_output_tokens},
        })

    return results


def extract_session_inputs(
    spans: list[OTelSpan],
    *,
    session_id_key: str = "session.id",
) -> list[dict[str, Any]]:
    """Extract session eval inputs — one row per session.

    Groups spans by session ID, orders traces chronologically,
    extracts user inputs and outputs across the session.

    Returns one dict per session with:
    - session_id: the session identifier
    - user_inputs: chronological list of user inputs
    - output_messages: chronological list of agent outputs
    - trace_count: number of traces in the session
    - tool_calls: all tool calls across the session
    """
    grouped = _group_spans(spans, session_id_key)

    results = []
    for session_id, session_spans in grouped.items():
        session_spans.sort(key=lambda s: s.start_time_ns)

        user_inputs: list[str] = []
        output_messages: list[str] = []
        tool_calls: list[str] = []
        trace_ids: set[str] = set()

        for span in session_spans:
            trace_ids.add(span.trace_id)
            if span.kind == "LLM":
                inp = span.attributes.get(_INPUT_VALUE_KEY, "")
                out = span.attributes.get(_OUTPUT_VALUE_KEY, "")
                if inp and inp not in user_inputs:
                    user_inputs.append(inp)
                if out:
                    output_messages.append(out)
            elif span.kind == "TOOL":
                tool_name = span.attributes.get(_TOOL_NAME_KEY, span.name)
                tool_input = span.attributes.get(_INPUT_VALUE_KEY, "")
                tool_calls.append(f"{tool_name}({tool_input})")

        results.append({
            "session_id": session_id,
            "user_inputs": json.dumps(user_inputs),
            "output_messages": json.dumps(output_messages),
            "trace_count": len(trace_ids),
            "tool_calls": json.dumps(tool_calls),
        })

    return results


def _group_spans_by_trace(spans: list[OTelSpan]) -> dict[str, list[OTelSpan]]:
    """Group spans by trace_id."""
    groups: dict[str, list[OTelSpan]] = {}
    for span in spans:
        groups.setdefault(span.trace_id, []).append(span)
    return groups


# ── Span tree reconstruction ──────────────────────────────────────


@dataclass
class SpanNode:
    """A node in the reconstructed span tree."""
    span: OTelSpan
    children: list["SpanNode"] = field(default_factory=list)

    @property
    def depth(self) -> int:
        if not self.children:
            return 0
        return 1 + max(c.depth for c in self.children)

    @property
    def size(self) -> int:
        return 1 + sum(c.size for c in self.children)

    def to_dict(
        self,
        *,
        include_input: bool = False,
        include_output: bool = True,
        max_content_chars: int = 500,
    ) -> dict[str, Any]:
        """Serialize to a judge-friendly dict with selective field inclusion."""
        d: dict[str, Any] = {
            "span_id": self.span.span_id,
            "type": self.span.kind,
            "name": self.span.attributes.get(_LANGGRAPH_NODE_KEY, self.span.name),
            "latency_ms": round(self.span.latency_ms, 1),
        }
        if self.span.kind == "LLM":
            d["model"] = self.span.attributes.get(_LLM_MODEL_KEY, "")
            d["tokens"] = {
                "input": self.span.attributes.get(_LLM_INPUT_TOKENS_KEY, 0),
                "output": self.span.attributes.get(_LLM_OUTPUT_TOKENS_KEY, 0),
            }
        if self.span.kind == "TOOL":
            d["tool_name"] = self.span.attributes.get(_TOOL_NAME_KEY, self.span.name)
            tool_input = self.span.attributes.get(_INPUT_VALUE_KEY, "")
            tool_output = self.span.attributes.get(_OUTPUT_VALUE_KEY, "")
            try:
                d["tool_args"] = json.loads(tool_input) if tool_input else {}
            except (json.JSONDecodeError, TypeError):
                d["tool_args"] = {"raw": tool_input[:max_content_chars]}
            d["tool_result"] = tool_output[:max_content_chars] if tool_output else ""

        if include_input:
            inp = self.span.attributes.get(_INPUT_VALUE_KEY, "")
            d["input"] = inp[:max_content_chars] if inp else ""
        if include_output:
            out = self.span.attributes.get(_OUTPUT_VALUE_KEY, "")
            d["output"] = out[:max_content_chars] if out else ""

        if self.children:
            d["children"] = [
                c.to_dict(
                    include_input=include_input,
                    include_output=include_output,
                    max_content_chars=max_content_chars,
                )
                for c in self.children
            ]
        return d


def build_span_tree(spans: list[OTelSpan]) -> list[SpanNode]:
    """Reconstruct parent-child tree from flat span list.

    Returns root nodes (spans with no parent or orphaned parent).
    Handles:
    - Orphaned spans (parent_id references missing span) → promoted to roots
    - Preserves chronological order within siblings
    - No recursion limit issues (iterative parent lookup)
    """
    nodes: dict[str, SpanNode] = {}
    for span in spans:
        nodes[span.span_id] = SpanNode(span=span)

    roots: list[SpanNode] = []
    for span_id, node in nodes.items():
        parent_id = node.span.parent_span_id
        if parent_id and parent_id in nodes:
            nodes[parent_id].children.append(node)
        else:
            roots.append(node)

    # Sort children by start time
    def _sort_children(node: SpanNode) -> None:
        node.children.sort(key=lambda c: c.span.start_time_ns)
        for child in node.children:
            _sort_children(child)

    for root in roots:
        _sort_children(root)

    roots.sort(key=lambda r: r.span.start_time_ns)
    return roots


# ── Tiered extraction strategy ────────────────────────────────────


class ExtractionMode:
    """Constants for extraction strategy selection."""
    FLAT = "flat"          # Arize-style: concatenate all spans chronologically
    TREE = "tree"          # Reconstruct parent-child, selective serialization
    CHUNKED = "chunked"    # Evaluate per-agent-boundary, then aggregate


def auto_select_extraction_mode(spans: list[OTelSpan]) -> str:
    """Automatically select extraction strategy based on trace complexity.

    Rules:
    - ≤10 spans, no sub-agents → FLAT (simple, fast, Arize-compatible)
    - 10-30 spans OR has sub-agents → TREE (preserve structure)
    - >30 spans OR >3 agent spans → CHUNKED (evaluate per boundary)
    """
    if not spans:
        return ExtractionMode.FLAT

    # NOTE: OpenInference does not currently define an "AGENT" span kind;
    # agent-level spans use "CHAIN". This check is forward-looking for when
    # the convention may add an explicit AGENT kind.
    agent_spans = [s for s in spans if s.kind == "AGENT"]
    tree = build_span_tree(spans)
    max_depth = max((r.depth for r in tree), default=0) if tree else 0

    if len(spans) <= 10 and len(agent_spans) == 0 and max_depth <= 2:
        return ExtractionMode.FLAT
    if len(spans) > 30 or len(agent_spans) > 3:
        return ExtractionMode.CHUNKED
    return ExtractionMode.TREE


def extract_for_judge(
    spans: list[OTelSpan],
    *,
    mode: str | None = None,
    max_tokens_budget: int = 15000,
    max_content_chars: int = 500,
) -> dict[str, Any]:
    """Extract trace data for judge using the appropriate strategy.

    Args:
        spans: List of OTel spans from one trace/session.
        mode: Extraction mode (flat/tree/chunked). None = auto-select.
        max_tokens_budget: Approximate token budget for judge context.
        max_content_chars: Max chars per individual content field.

    Returns:
        Dict with keys:
        - mode: the extraction mode used
        - representation: the extracted data (format depends on mode)
        - metadata: aggregate stats (span_count, depth, agent_count, etc.)
    """
    if mode is None:
        mode = auto_select_extraction_mode(spans)

    metadata = {
        "span_count": len(spans),
        "mode": mode,
        "llm_spans": len([s for s in spans if s.kind == "LLM"]),
        "tool_spans": len([s for s in spans if s.kind == "TOOL"]),
        "agent_spans": len([s for s in spans if s.kind == "AGENT"]),
    }

    if mode == ExtractionMode.FLAT:
        events, aggregate = _spans_to_events(spans)
        compressed = compress_trace_for_judge(events, max_events=50)
        metadata.update(aggregate)
        return {"mode": mode, "representation": compressed, "metadata": metadata}

    if mode == ExtractionMode.TREE:
        tree = build_span_tree(spans)
        metadata["max_depth"] = max((r.depth for r in tree), default=0)
        # Selective serialization — strip LLM input messages (redundant accumulated context)
        tree_data = [
            root.to_dict(
                include_input=False,
                include_output=True,
                max_content_chars=max_content_chars,
            )
            for root in tree
        ]
        # Estimate token size and truncate if needed
        serialized = json.dumps(tree_data)
        est_tokens = len(serialized) // 4  # rough estimate: 4 chars per token
        if est_tokens > max_tokens_budget:
            # Reduce content chars proportionally
            ratio = max_tokens_budget / est_tokens
            reduced_chars = max(100, int(max_content_chars * ratio))
            tree_data = [
                root.to_dict(
                    include_input=False,
                    include_output=True,
                    max_content_chars=reduced_chars,
                )
                for root in tree
            ]
        return {"mode": mode, "representation": tree_data, "metadata": metadata}

    if mode == ExtractionMode.CHUNKED:
        # Evaluate per agent boundary
        tree = build_span_tree(spans)
        metadata["max_depth"] = max((r.depth for r in tree), default=0)
        chunks: list[dict[str, Any]] = []
        for root in tree:
            chunk = {
                "agent": root.span.attributes.get(_LANGGRAPH_NODE_KEY, root.span.name),
                "type": root.span.kind,
                "span_count": root.size,
                "tree": root.to_dict(
                    include_input=False,
                    include_output=True,
                    max_content_chars=max_content_chars,
                ),
            }
            chunks.append(chunk)
        return {"mode": mode, "representation": chunks, "metadata": metadata}

    raise ValueError(f"Unknown extraction mode: {mode}")
