#!/usr/bin/env python3
"""Langfuse -> ASSERT bridge: turn existing traces into judgeable conversations.

    Langfuse public API -> OTLP JSON -> inference_set.jsonl
                                       -> assert-ai run --force-stage judge

Why a real converter is needed (all verified, see README.md "Why Tier-2"):

  * Langfuse's OTLP surface is **ingest-only**. There is no corresponding OTLP
    read path, so "export the OTLP you sent" is not an option.
  * The read API returns Langfuse's own Trace/Observation model: ISO-8601
    timestamps, a flat JSON ``metadata`` blob, a 10-value ``ObservationType``
    enum, and first-class ``input``/``output`` fields. ASSERT wants OTLP JSON:
    ``resourceSpans -> scopeSpans -> spans`` with ``startTimeUnixNano`` and a
    typed ``attributes: [{key, value: {stringValue|intValue|...}}]`` array.
  * On ingestion Langfuse *deletes* the content-bearing attribute keys ASSERT
    reads (``input.value``, ``output.value``, ``gen_ai.input.messages``,
    ``gen_ai.output.messages``, ``gen_ai.tool.call.arguments``,
    ``gen_ai.tool.call.result``) and moves their content into ``observation.input``
    / ``observation.output``. So even a trace that was *originally* perfect
    OpenInference comes back missing exactly the keys ASSERT's parser needs.
    (Verified in Langfuse source: ``OtelIngestionProcessor.extractInputAndOutput``,
    ``potentialInputOutputKeys``.)
  * Spans emitted by the Langfuse SDK itself carry **no** raw OTel attributes at
    all -- ``metadata.attributes`` is omitted when the instrumentation scope name
    starts with ``langfuse-sdk``. Those spans must be synthesized from Langfuse's
    native fields alone.

So the bridge has two paths per observation:

  1. **preserve** -- ``metadata.attributes`` is present (span arrived from external
     OTel instrumentation). Reuse the original attribute keys, re-type the values
     Langfuse stringified, and re-inject the stripped content keys.
  2. **synthesize** -- no ``metadata.attributes`` (Langfuse-SDK-native span).
     Build OpenInference attributes from ``type`` / ``model`` / ``usageDetails`` /
     ``input`` / ``output``.

Stdlib only. No Langfuse SDK, no ASSERT import required for the OTLP conversion
(ASSERT is imported only by ``--emit-inference-set``).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# --------------------------------------------------------------------------- #
# ASSERT-side attribute keys (ground truth: assert_ai/core/otel.py).
# --------------------------------------------------------------------------- #
SPAN_KIND_KEY = "openinference.span.kind"
INPUT_VALUE_KEY = "input.value"
OUTPUT_VALUE_KEY = "output.value"
LLM_MODEL_KEY = "llm.model_name"
LLM_INPUT_TOKENS_KEY = "llm.token_count.prompt"
LLM_OUTPUT_TOKENS_KEY = "llm.token_count.completion"
TOOL_NAME_KEY = "tool.name"
SESSION_ID_KEY = "session.id"

# Content keys Langfuse deletes on ingestion and we must re-inject.
# Source: OtelIngestionProcessor.ts -> extractInputAndOutput -> potentialInputOutputKeys.
LANGFUSE_STRIPPED_CONTENT_KEYS = (
    "input.value",
    "output.value",
    "gen_ai.input.messages",
    "gen_ai.output.messages",
    "gen_ai.tool.call.arguments",
    "gen_ai.tool.call.result",
    "mlflow.spanInputs",
    "mlflow.spanOutputs",
    "traceloop.entity.input",
    "traceloop.entity.output",
    "ai.prompt.messages",
    "ai.response.text",
)

# ASSERT's OpenInference branch (_openinference_span_to_events) has exactly three
# real cases -- LLM, TOOL, CHAIN -- plus a catch-all that renders the span as an
# *assistant* message. Any other OpenInference kind (RETRIEVER, AGENT, GUARDRAIL,
# ...) therefore either gets misattributed to the agent or, if it carries no
# input/output, is dropped silently. So every span kind is normalized into the
# three ASSERT actually models.
ASSERT_SPAN_KINDS = ("LLM", "TOOL", "CHAIN")

# Langfuse ObservationType -> openinference.span.kind, and the normalization
# applied to preserved OpenInference kinds. Inverse of Langfuse's own
# ObservationTypeMapper, with two deliberate judge-quality adjustments
# (both documented in README.md):
#
#   RETRIEVER -> TOOL  : makes retrieved documents land as tool evidence the judge
#                        can cite, instead of being dropped by ASSERT's catch-all.
#   AGENT     -> CHAIN : an AGENT span's output usually duplicates the text its
#                        child GENERATION span already produced. CHAIN keeps node
#                        attribution without emitting a second assistant turn that
#                        would double-count the agent's answer.
OBSERVATION_TYPE_TO_SPAN_KIND = {
    "GENERATION": "LLM",
    "TOOL": "TOOL",
    "GUARDRAIL": "TOOL",
    "EVALUATOR": "TOOL",
    "RETRIEVER": "TOOL",
    "AGENT": "CHAIN",
    "CHAIN": "CHAIN",
    "SPAN": "CHAIN",
    "EMBEDDING": "CHAIN",
    "EVENT": "CHAIN",
}

# OpenInference span kinds that ASSERT cannot model, mapped to ones it can.
OPENINFERENCE_KIND_NORMALIZATION = {
    "RETRIEVER": "TOOL",
    "GUARDRAIL": "TOOL",
    "EVALUATOR": "TOOL",
    "RERANKER": "TOOL",
    "AGENT": "CHAIN",
    "EMBEDDING": "CHAIN",
}


def normalize_span_kind(kind: str) -> str:
    """Coerce any OpenInference span kind into one ASSERT's parser models."""
    upper = (kind or "").upper()
    if upper in ASSERT_SPAN_KINDS:
        return upper
    return OPENINFERENCE_KIND_NORMALIZATION.get(upper, "CHAIN")

# Synthetic span names used to work around ASSERT's assistant-only emission.
USER_TURN_TOOL_NAME = "user_message"
SYSTEM_TURN_TOOL_NAME = "system_prompt"

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_HEX16 = re.compile(r"^[0-9a-f]{16}$")


# --------------------------------------------------------------------------- #
# ID + timestamp normalization
# --------------------------------------------------------------------------- #
def to_trace_id(value: str) -> str:
    """Return a 32-char lowercase-hex OTel trace id.

    Langfuse stores OTel-ingested ids as hex (pass through) but Langfuse-SDK ids
    as UUIDs. Hash non-hex ids deterministically so the same Langfuse id always
    maps to the same OTel id across runs.
    """
    v = (value or "").strip().lower()
    if _HEX32.match(v):
        return v
    compact = v.replace("-", "")
    if _HEX32.match(compact):
        return compact
    return hashlib.blake2b(v.encode("utf-8"), digest_size=16).hexdigest()


def to_span_id(value: str) -> str:
    """Return a 16-char lowercase-hex OTel span id (same determinism guarantee)."""
    v = (value or "").strip().lower()
    if _HEX16.match(v):
        return v
    return hashlib.blake2b(v.encode("utf-8"), digest_size=8).hexdigest()


def iso_to_unix_nano(value: Any, *, default: int = 0) -> int:
    """Convert a Langfuse ISO-8601 timestamp to OTel unix nanoseconds.

    Langfuse persists ``startTimeISO`` / ``endTimeISO`` (verified in
    OtelIngestionProcessor.ts); OTLP requires integer nanoseconds.
    """
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        # Already epoch-ish. Heuristic on magnitude: s / ms / us / ns.
        v = float(value)
        for threshold, scale in ((1e11, 1e9), (1e14, 1e6), (1e17, 1e3)):
            if v < threshold:
                return int(v * scale)
        return int(v)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return default
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


# --------------------------------------------------------------------------- #
# OTLP typed-value encoding
# --------------------------------------------------------------------------- #
def to_otlp_value(value: Any) -> dict[str, Any]:
    """Wrap a Python value in an OTLP AnyValue.

    Mirrors what ``assert_ai.core.otel._flatten_attributes`` can read back:
    stringValue / intValue / doubleValue / boolValue / arrayValue. Anything else
    (dict, nested structure) is JSON-encoded to a string, which is exactly how
    ASSERT's parser expects structured content such as ``input.value``.
    """
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, (list, tuple)):
        if all(isinstance(v, (str, int, float, bool)) and not isinstance(v, dict) for v in value):
            return {"arrayValue": {"values": [to_otlp_value(v) for v in value]}}
        return {"stringValue": json.dumps(value, ensure_ascii=False)}
    if isinstance(value, dict):
        return {"stringValue": json.dumps(value, ensure_ascii=False)}
    return {"stringValue": "" if value is None else str(value)}


def attrs_to_otlp(attrs: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"key": k, "value": to_otlp_value(v)} for k, v in attrs.items() if v is not None]


def retype_langfuse_attribute(key: str, value: Any) -> Any:
    """Undo Langfuse's blanket stringification of span attributes.

    Langfuse stores every non-string attribute value as ``JSON.stringify(value)``
    (verified: ``typeof value === "string" ? value : JSON.stringify(value)``), so
    an integer token count comes back as ``"98"`` and a boolean as ``"true"``.
    Restore ints/floats/bools for the numeric + boolean keys ASSERT reads;
    leave everything else alone.
    """
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped in ("true", "false"):
        return stripped == "true"
    if _looks_numeric(stripped):
        try:
            return int(stripped)
        except ValueError:
            try:
                return float(stripped)
            except ValueError:
                return value
    return value


def _looks_numeric(text: str) -> bool:
    if not text or text in ("-", "+"):
        return False
    body = text[1:] if text[0] in "+-" else text
    return body.replace(".", "", 1).isdigit()


# --------------------------------------------------------------------------- #
# Langfuse field access (v1 `Observation` and v2 `ObservationV2` both supported)
# --------------------------------------------------------------------------- #
def _maybe_json(value: Any) -> Any:
    """Parse a Langfuse input/output payload.

    v1 (``GET /api/public/traces/{id}``) returns parsed JSON; v2
    (``GET /api/public/v2/observations``) returns a raw string. Handle both.
    """
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in ("{", "[") or text in ("null", "true", "false"):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return value
    return value


def _as_text(value: Any) -> str:
    """Render a Langfuse input/output payload as judge-readable text."""
    parsed = _maybe_json(value)
    if parsed is None:
        return ""
    if isinstance(parsed, str):
        return parsed
    if isinstance(parsed, dict):
        # OpenAI-style single message.
        content = parsed.get("content")
        if isinstance(content, str) and content:
            return content
        if isinstance(content, list):
            return _content_parts_to_text(content)
        for key in ("text", "output", "response", "completion", "result"):
            inner = parsed.get(key)
            if isinstance(inner, str) and inner:
                return inner
        return json.dumps(parsed, ensure_ascii=False)
    if isinstance(parsed, list):
        texts = [_as_text(item) for item in parsed]
        joined = "\n".join(t for t in texts if t)
        return joined or json.dumps(parsed, ensure_ascii=False)
    return str(parsed)


def _content_parts_to_text(parts: list[Any]) -> str:
    out: list[str] = []
    for part in parts:
        if isinstance(part, str):
            out.append(part)
        elif isinstance(part, dict):
            text = part.get("text") or part.get("content")
            if isinstance(text, str):
                out.append(text)
    return "\n".join(t for t in out if t)


def observation_model(obs: dict[str, Any]) -> str:
    """v1 uses ``model``; v2 uses ``providedModelName``."""
    return str(obs.get("model") or obs.get("providedModelName") or "")


def observation_tokens(obs: dict[str, Any]) -> tuple[int, int]:
    """Return (input_tokens, output_tokens) across v1/v2 and legacy shapes."""
    details = obs.get("usageDetails")
    if isinstance(details, dict):
        inp = details.get("input")
        out = details.get("output")
        if inp is not None or out is not None:
            return int(inp or 0), int(out or 0)
    usage = obs.get("usage")
    if isinstance(usage, dict):
        inp = usage.get("input", usage.get("promptTokens"))
        out = usage.get("output", usage.get("completionTokens"))
        if inp is not None or out is not None:
            return int(inp or 0), int(out or 0)
    inp = obs.get("inputUsage")
    out = obs.get("outputUsage")
    if inp is not None or out is not None:
        return int(inp or 0), int(out or 0)
    return 0, 0


def observation_attributes(obs: dict[str, Any]) -> dict[str, Any]:
    """Return raw OTel attributes Langfuse preserved, if any.

    Present at ``metadata.attributes`` for spans that arrived from external OTel
    instrumentation; absent entirely for Langfuse-SDK-native spans.
    """
    metadata = obs.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    attributes = metadata.get("attributes")
    if not isinstance(attributes, dict):
        return {}
    return {k: retype_langfuse_attribute(k, v) for k, v in attributes.items()}


# --------------------------------------------------------------------------- #
# Message extraction (for the synthetic user/system turns)
# --------------------------------------------------------------------------- #
def extract_messages(payload: Any) -> list[dict[str, str]]:
    """Pull ``[{role, content}]`` message dicts out of a Langfuse input payload."""
    parsed = _maybe_json(payload)
    candidates: list[Any] = []
    if isinstance(parsed, list):
        candidates = parsed
    elif isinstance(parsed, dict):
        for key in ("messages", "input", "prompt", "chat_history"):
            inner = parsed.get(key)
            if isinstance(inner, list):
                candidates = inner
                break
        else:
            if "role" in parsed:
                candidates = [parsed]
    messages: list[dict[str, str]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").lower()
        if role not in ("user", "system", "assistant", "tool"):
            continue
        content = item.get("content")
        text = _content_parts_to_text(content) if isinstance(content, list) else content
        if not isinstance(text, str) or not text.strip():
            continue
        messages.append({"role": role, "content": text})
    return messages


def _reconcile_message_sequence(
    history: list[dict[str, str]],
    incoming: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Merge one model input into session history and return its new messages.

    Langfuse traces may contain the full conversation, a trailing slice of it,
    or only the current turn. Reconcile by removing a repeated leading system
    prompt, then the longest suffix of prior history that matches the incoming
    prefix. Sequence overlap, rather than session-wide content counts, keeps a
    later identical user turn when the preceding assistant response differs.
    """
    messages = [message for message in incoming if message["role"] != "tool"]
    if not messages:
        return []

    system_overlap = 0
    while (
        system_overlap < len(history)
        and system_overlap < len(messages)
        and history[system_overlap]["role"] == "system"
        and history[system_overlap] == messages[system_overlap]
    ):
        system_overlap += 1

    remaining = messages[system_overlap:]
    overlap = 0
    for size in range(min(len(history), len(remaining)), 0, -1):
        if history[-size:] == remaining[:size]:
            overlap = size
            break

    novel = remaining[overlap:]
    history.extend(novel)
    return novel


def _append_generation_output(
    history: list[dict[str, str]],
    payload: Any,
) -> None:
    """Append assistant output so a repeated next user turn stays distinguishable."""
    messages = [
        message
        for message in extract_messages(payload)
        if message["role"] in ("system", "user", "assistant")
    ]
    if messages:
        history.extend(messages)
        return

    parsed = _maybe_json(payload)
    if isinstance(parsed, str) and parsed.strip():
        history.append({"role": "assistant", "content": parsed})
    elif isinstance(parsed, dict):
        for key in ("content", "text", "output", "response", "completion", "result"):
            text = parsed.get(key)
            if isinstance(text, str) and text.strip():
                history.append({"role": "assistant", "content": text})
                break


# --------------------------------------------------------------------------- #
# Observation -> OTLP span
# --------------------------------------------------------------------------- #
def observation_to_span(
    obs: dict[str, Any],
    *,
    trace_id_hex: str,
    session_id: str,
    keep_native_convention: bool,
) -> dict[str, Any]:
    """Convert one Langfuse observation into an OTLP span dict."""
    obs_type = str(obs.get("type") or "SPAN").upper()
    span_kind = OBSERVATION_TYPE_TO_SPAN_KIND.get(obs_type, "CHAIN")

    preserved = observation_attributes(obs)
    attrs: dict[str, Any] = {}
    provenance = "synthesize"
    original_kind = ""

    if preserved and keep_native_convention:
        provenance = "preserve"
        # Drop content keys Langfuse already emptied; they'd be stale or absent
        # anyway, and we re-inject authoritative values from input/output below.
        attrs = {k: v for k, v in preserved.items() if k not in LANGFUSE_STRIPPED_CONTENT_KEYS}
        if SPAN_KIND_KEY in preserved:
            # Honor the source runtime's own classification, but normalize kinds
            # ASSERT's OpenInference branch cannot model -- otherwise a RETRIEVER
            # span is dropped and an AGENT span duplicates its child's answer.
            original_kind = str(preserved[SPAN_KIND_KEY]).upper()
            span_kind = normalize_span_kind(original_kind)
        else:
            # gen_ai.* only. ASSERT prefers OpenInference when the key exists, and
            # the gen_ai content attributes were stripped by Langfuse, so pin an
            # OpenInference kind and let the re-injected input/output carry content.
            span_kind = normalize_span_kind(span_kind)
    else:
        span_kind = normalize_span_kind(span_kind)

    attrs[SPAN_KIND_KEY] = span_kind

    raw_input = obs.get("input")
    raw_output = obs.get("output")

    if span_kind == "LLM":
        model = observation_model(obs)
        if model:
            attrs.setdefault(LLM_MODEL_KEY, model)
        in_tok, out_tok = observation_tokens(obs)
        if in_tok:
            attrs[LLM_INPUT_TOKENS_KEY] = in_tok
        if out_tok:
            attrs[LLM_OUTPUT_TOKENS_KEY] = out_tok
        # ASSERT's OpenInference LLM branch reads output.value for the assistant
        # turn. Langfuse stripped this key on ingestion, so re-inject it.
        output_text = _as_text(raw_output)
        if output_text:
            attrs[OUTPUT_VALUE_KEY] = output_text
        if raw_input is not None:
            attrs[INPUT_VALUE_KEY] = _serialize(raw_input)

    elif span_kind == "TOOL":
        tool_name = (
            attrs.get(TOOL_NAME_KEY)
            or preserved.get("gen_ai.tool.name")
            or obs.get("name")
            or "tool"
        )
        attrs[TOOL_NAME_KEY] = str(tool_name)
        attrs[INPUT_VALUE_KEY] = _serialize(raw_input if raw_input is not None else {})
        attrs[OUTPUT_VALUE_KEY] = _as_text(raw_output)

    else:  # CHAIN and everything else: node attribution only, no transcript turn.
        attrs.pop(INPUT_VALUE_KEY, None)
        attrs.pop(OUTPUT_VALUE_KEY, None)

    # ASSERT groups spans into conversations by this attribute (default
    # --group-by session.id). Always present so grouping never silently falls
    # back to per-trace grouping when a developer uses Langfuse sessions.
    attrs[SESSION_ID_KEY] = session_id

    # Provenance is carried so the README's claims are auditable from the output.
    attrs["langfuse.observation.id"] = str(obs.get("id") or "")
    attrs["langfuse.observation.type"] = obs_type
    attrs["assert.bridge.provenance"] = provenance
    if original_kind and original_kind != span_kind:
        attrs["assert.bridge.original_span_kind"] = original_kind

    start_ns = iso_to_unix_nano(obs.get("startTime"))
    end_ns = iso_to_unix_nano(obs.get("endTime"), default=start_ns) or start_ns

    span: dict[str, Any] = {
        "traceId": trace_id_hex,
        "spanId": to_span_id(str(obs.get("id") or "")),
        "name": str(obs.get("name") or obs_type.lower()),
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "attributes": attrs_to_otlp(attrs),
        "status": {"code": _status_code(obs)},
    }
    parent = obs.get("parentObservationId")
    if parent:
        span["parentSpanId"] = to_span_id(str(parent))
    return span


def _serialize(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _status_code(obs: dict[str, Any]) -> str:
    level = str(obs.get("level") or "").upper()
    return "STATUS_CODE_ERROR" if level == "ERROR" else "STATUS_CODE_OK"


# --------------------------------------------------------------------------- #
# Synthetic user / system turns
# --------------------------------------------------------------------------- #
def synthetic_turn_span(
    *,
    role: str,
    text: str,
    trace_id_hex: str,
    session_id: str,
    seed: str,
    start_ns: int,
) -> dict[str, Any]:
    """Emit a labelled turn that survives ASSERT's assistant-only parser.

    ``assert_ai.core.otel`` never produces ``role="user"``: every ``add_message``
    edit it emits is hardcoded to ``role="assistant"`` (three call sites, all
    assistant). A user turn mapped through the normal path would therefore be
    attributed to the *agent* -- actively dangerous for a judge deciding whether
    the agent complied, since an adversarial user instruction would read as the
    agent's own words.

    So we emit a TOOL span instead. ASSERT renders it as
    ``[Tool call: user_message({"role": "user"}) -> <text>]`` with ``role="tool"``
    -- external input, correctly *not* attributed to the agent. Explicit and safe.

    ``--emit-inference-set`` upgrades these back into true ``role="user"`` /
    ``set_system_message`` transcript edits.
    """
    tool_name = USER_TURN_TOOL_NAME if role == "user" else SYSTEM_TURN_TOOL_NAME
    attrs = {
        SPAN_KIND_KEY: "TOOL",
        TOOL_NAME_KEY: tool_name,
        INPUT_VALUE_KEY: json.dumps({"role": role}, ensure_ascii=False),
        OUTPUT_VALUE_KEY: text,
        SESSION_ID_KEY: session_id,
        "assert.bridge.synthetic_role": role,
        "assert.bridge.provenance": "synthetic",
    }
    return {
        "traceId": trace_id_hex,
        "spanId": to_span_id(f"synthetic:{seed}"),
        "name": tool_name,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(start_ns),
        "attributes": attrs_to_otlp(attrs),
        "status": {"code": "STATUS_CODE_OK"},
    }


# --------------------------------------------------------------------------- #
# Trace -> spans
# --------------------------------------------------------------------------- #
def trace_to_spans(
    trace: dict[str, Any],
    *,
    keep_native_convention: bool = True,
    synthesize_turns: bool | None = None,
    message_history: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Convert one Langfuse trace (with observations) to a list of OTLP spans.

    ``synthesize_turns=None`` (the default) auto-detects: synthesis is enabled
    only on an ASSERT build that cannot recover ``role="user"`` on its own.
    See :func:`assert_recovers_input_messages`.
    """
    if synthesize_turns is None:
        synthesize_turns = not assert_recovers_input_messages()
    trace_id_hex = to_trace_id(str(trace.get("id") or ""))
    session_id = str(trace.get("sessionId") or trace.get("id") or trace_id_hex)
    observations = [o for o in (trace.get("observations") or []) if isinstance(o, dict)]
    observations.sort(key=lambda o: (str(o.get("startTime") or ""), str(o.get("id") or "")))

    spans: list[dict[str, Any]] = []
    message_history = message_history if message_history is not None else []

    def append_unseen_turns(messages: list[dict[str, str]], before_ns: int, seed: str) -> int:
        novel = _reconcile_message_sequence(message_history, messages)
        relevant = [message for message in novel if message["role"] in ("user", "system")]
        added = 0
        for position, msg in enumerate(relevant):
            spans.append(
                synthetic_turn_span(
                    role=msg["role"],
                    text=msg["content"],
                    trace_id_hex=trace_id_hex,
                    session_id=session_id,
                    seed=f"{seed}:{position}",
                    # Keep system/user order stable immediately before the LLM
                    # call that consumed the messages.
                    start_ns=max(before_ns - 1000 * (len(relevant) - position), 0),
                )
            )
            added += 1
        return added

    generation_turns_seen = False
    for obs in observations:
        obs_start = iso_to_unix_nano(obs.get("startTime"))
        if synthesize_turns and str(obs.get("type") or "").upper() == "GENERATION":
            messages = extract_messages(obs.get("input"))
            if any(message["role"] in ("user", "system") for message in messages):
                generation_turns_seen = True
                append_unseen_turns(
                    messages,
                    obs_start,
                    f"{trace_id_hex}:{obs.get('id')}",
                )
                _append_generation_output(message_history, obs.get("output"))
        spans.append(
            observation_to_span(
                obs,
                trace_id_hex=trace_id_hex,
                session_id=session_id,
                keep_native_convention=keep_native_convention,
            )
        )

    if synthesize_turns and not generation_turns_seen:
        # Some traces have no GENERATION observation. Fall back to trace.input.
        append_unseen_turns(
            extract_messages(trace.get("input")) or _fallback_user_turn(trace),
            iso_to_unix_nano(trace.get("timestamp")),
            f"{trace_id_hex}:trace",
        )
        _append_generation_output(message_history, trace.get("output"))
    return spans


def _fallback_user_turn(trace: dict[str, Any]) -> list[dict[str, str]]:
    """When trace.input isn't a message array, treat its text as one user turn."""
    text = _as_text(trace.get("input"))
    return [{"role": "user", "content": text}] if text.strip() else []


# --------------------------------------------------------------------------- #
# Top-level conversion
# --------------------------------------------------------------------------- #
def convert_traces(
    traces: Iterable[dict[str, Any]],
    *,
    keep_native_convention: bool = True,
    synthesize_turns: bool | None = None,
    service_name: str = "langfuse-export",
) -> dict[str, Any]:
    """Convert Langfuse traces into a single OTLP JSON export document."""
    if synthesize_turns is None:
        synthesize_turns = not assert_recovers_input_messages()
    spans: list[dict[str, Any]] = []
    message_history_by_session: dict[str, list[dict[str, str]]] = {}
    ordered_traces = sorted(
        (trace for trace in traces if isinstance(trace, dict)),
        key=lambda trace: (
            str(trace.get("timestamp") or ""),
            str(trace.get("id") or ""),
        ),
    )
    for trace in ordered_traces:
        if not isinstance(trace, dict):
            continue
        session_id = str(trace.get("sessionId") or trace.get("id") or "")
        spans.extend(
            trace_to_spans(
                trace,
                keep_native_convention=keep_native_convention,
                synthesize_turns=synthesize_turns,
                message_history=message_history_by_session.setdefault(session_id, []),
            )
        )
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": attrs_to_otlp(
                        {
                            "service.name": service_name,
                            "telemetry.sdk.name": "langfuse-to-assert-bridge",
                        }
                    )
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "langfuse_to_assert", "version": "1.0.0"},
                        "spans": spans,
                    }
                ],
            }
        ]
    }


# --------------------------------------------------------------------------- #
# Input loading: file or live Langfuse API
# --------------------------------------------------------------------------- #
def load_traces_from_file(path: Path) -> list[dict[str, Any]]:
    """Accept a single trace, a bare list, or an API envelope ``{"data": [...]}``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return [t for t in data["data"] if isinstance(t, dict)]
    if isinstance(data, list):
        return [t for t in data if isinstance(t, dict)]
    if isinstance(data, dict):
        return [data]
    raise ValueError(f"Unrecognized Langfuse export shape in {path}")


class LangfuseClient:
    """Minimal Langfuse public-API client (stdlib only).

    Auth is HTTP Basic with public key as username and secret key as password.
    """

    def __init__(self, host: str, public_key: str, secret_key: str, timeout: int = 60):
        self.host = host.rstrip("/")
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
        }
        self.timeout = timeout

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.host}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url = f"{url}?{urllib.parse.urlencode(clean)}"
        req = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def list_traces(
        self,
        *,
        limit: int = 20,
        session_id: str | None = None,
        user_id: str | None = None,
        tags: str | None = None,
        from_timestamp: str | None = None,
    ) -> list[dict[str, Any]]:
        payload = self._get(
            "/api/public/traces",
            {
                "limit": limit,
                "page": 1,
                "sessionId": session_id,
                "userId": user_id,
                "tags": tags,
                "fromTimestamp": from_timestamp,
            },
        )
        return payload.get("data", []) if isinstance(payload, dict) else []

    def get_trace(self, trace_id: str) -> dict[str, Any]:
        """GET /api/public/traces/{traceId} -> TraceWithFullDetails (has observations)."""
        return self._get(f"/api/public/traces/{urllib.parse.quote(trace_id, safe='')}")


def fetch_traces(
    client: LangfuseClient,
    *,
    limit: int,
    session_id: str | None,
    user_id: str | None,
    tags: str | None,
    from_timestamp: str | None,
) -> list[dict[str, Any]]:
    """List traces, then fetch each in full so observations are included.

    ``GET /api/public/traces`` returns ``TraceWithDetails`` (no observations);
    only ``GET /api/public/traces/{id}`` returns ``TraceWithFullDetails``.
    """
    summaries = client.list_traces(
        limit=limit,
        session_id=session_id,
        user_id=user_id,
        tags=tags,
        from_timestamp=from_timestamp,
    )
    full: list[dict[str, Any]] = []
    for summary in summaries:
        trace_id = summary.get("id")
        if not trace_id:
            continue
        try:
            full.append(client.get_trace(str(trace_id)))
        except urllib.error.HTTPError as exc:
            print(f"  ! skipping trace {trace_id}: HTTP {exc.code}", file=sys.stderr)
    return full


# --------------------------------------------------------------------------- #
# inference_set.jsonl emission (calls ASSERT's real parser, then repairs it)
# --------------------------------------------------------------------------- #
def emit_inference_set(
    otlp_path: Path,
    out_path: Path,
    *,
    group_by: str = "session.id",
    behavior: str = "langfuse_import",
    target: str = "langfuse-trace",
) -> list[dict[str, Any]]:
    """Run ASSERT's real ``parse_otel_traces`` and repair three known gaps.

    1. ``parse_otel_traces`` puts ``type`` inside ``metadata`` and emits no
       ``test_case_id``. ASSERT's viewer read-model requires a *top-level*
       ``type`` in {"prompt","scenario"} and a non-empty ``test_case_id``
       (``viewer_read_model._kind_and_test_case_id``), and the judge's resume
       cache keys on ``(type, test_case_id)`` -- with both blank, every re-run
       re-judges every row and burns tokens. We set ``type="scenario"`` and a
       stable ``test_case_id`` derived from the session id.
    2. The parser never emits ``role="user"``. We convert the synthetic
       ``user_message`` / ``system_prompt`` tool_call events back into real
       ``add_message`` (role=user) / ``set_system_message`` edits, which
       ``assert_ai.core.transcript`` fully supports.
    3. ``behavior`` / ``target`` are absent; the judge reads them for transcript
       metadata. We populate both.
    """
    sys.path.insert(0, str(_assert_repo_root()))
    from assert_ai.core.otel import parse_otel_traces  # noqa: PLC0415

    rows = parse_otel_traces(otlp_path, group_by=group_by)
    repaired: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        metadata = row.get("metadata") or {}
        session_id = str(metadata.get("session_id") or f"session-{index}")
        events = _restore_roles(row.get("events") or [])
        # Newer ASSERT builds already stamp a top-level type/test_case_id. Keep
        # theirs so the judge's resume cache key matches between this path and
        # the stock `assert-ai judge-traces` path; only fill in when absent.
        repaired.append(
            {
                "type": row.get("type") or "scenario",
                "test_case_id": row.get("test_case_id") or _slug(session_id),
                "behavior": behavior,
                "target": target,
                "tester_model": "",
                "metadata": metadata,
                "events": events,
                "raw": row.get("raw") or {},
            }
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for row in repaired:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return repaired


def _restore_roles(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn synthetic turn tool_calls back into properly-roled transcript edits."""
    restored: list[dict[str, Any]] = []
    for event in events:
        edit = event.get("edit") or {}
        tool_name = edit.get("tool_name")
        if edit.get("type") == "tool_call" and tool_name in (
            USER_TURN_TOOL_NAME,
            SYSTEM_TURN_TOOL_NAME,
        ):
            content = edit.get("tool_result") or ""
            if tool_name == USER_TURN_TOOL_NAME:
                restored.append(
                    {
                        "view": event.get("view", ["target", "combined"]),
                        "actor": "tester",
                        "edit": {
                            "type": "add_message",
                            "message": {"role": "user", "content": content},
                        },
                    }
                )
            else:
                restored.append(
                    {
                        "view": event.get("view", ["target", "combined"]),
                        "actor": "tester",
                        "edit": {
                            "type": "set_system_message",
                            "message": {"role": "system", "content": content},
                        },
                    }
                )
            continue
        restored.append(event)
    return restored


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return cleaned[:64] or "session"


def assert_recovers_input_messages() -> bool:
    """Does the installed ASSERT recover ``role="user"`` from OTel spans itself?

    Historically ``assert_ai.core.otel`` hardcoded ``role="assistant"`` at every
    ``add_message`` call site, so an adversarial user instruction imported from a
    trace was attributed to the *agent*. This bridge worked around that by
    emitting each user/system turn as a synthetic TOOL span.

    ASSERT now recovers the input side natively (``_EventAccumulator``
    grows an ``emit_input_messages`` method). When that is present the
    workaround is not just unnecessary, it is harmful: the synthetic TOOL span
    and the natively recovered ``role="user"`` event would both land in the
    transcript and every user turn would appear twice.

    So probe for the capability instead of guessing.
    """
    try:
        sys.path.insert(0, str(_assert_repo_root()))
        from assert_ai.core import otel as assert_otel  # noqa: PLC0415
    except Exception:  # pragma: no cover - ASSERT not importable at convert time
        return False
    accumulator = getattr(assert_otel, "_EventAccumulator", None)
    return bool(accumulator is not None and hasattr(accumulator, "emit_input_messages"))


def _assert_repo_root() -> Path:
    """Locate the ASSERT repo so ``assert_ai`` is importable when not installed."""
    env = os.environ.get("ASSERT_REPO")
    if env and (Path(env) / "assert_ai").is_dir():
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "assert_ai").is_dir():
            return parent
    return here.parent


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="langfuse_to_assert",
        description="Convert Langfuse traces into ASSERT-judgeable OTLP JSON.",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", type=Path, help="Langfuse API JSON dump (trace, list, or {data:[...]}).")
    src.add_argument("--api", action="store_true", help="Fetch live from the Langfuse public API.")

    p.add_argument("--output", type=Path, default=Path("out/langfuse_traces_otlp.json"))
    p.add_argument("--emit-inference-set", type=Path, default=None,
                   help="Also write a repaired inference_set.jsonl using ASSERT's real parser.")
    p.add_argument("--group-by", default="session.id", help="Grouping attribute (matches assert-ai judge-traces).")
    p.add_argument("--behavior", default="langfuse_import", help="behavior label for emitted inference rows.")

    p.add_argument("--host", default=os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"))
    p.add_argument("--public-key", default=os.environ.get("LANGFUSE_PUBLIC_KEY", ""))
    p.add_argument("--secret-key", default=os.environ.get("LANGFUSE_SECRET_KEY", ""))
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--session-id", default=None)
    p.add_argument("--user-id", default=None)
    p.add_argument("--tags", default=None)
    p.add_argument("--from-timestamp", default=None, help="ISO-8601 lower bound.")

    turns = p.add_mutually_exclusive_group()
    turns.add_argument("--no-synthetic-turns", action="store_true",
                       help="Never emit synthetic user/system turn spans.")
    turns.add_argument("--synthetic-turns", action="store_true",
                       help="Always emit synthetic user/system turn spans (needed only on an "
                            "ASSERT build whose OTel parser cannot recover role=user itself). "
                            "Default: auto-detected.")
    p.add_argument("--force-synthesize", action="store_true",
                   help="Ignore preserved metadata.attributes; always rebuild OpenInference attrs.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.synthetic_turns:
        synthesize_turns, turn_mode = True, "forced on (--synthetic-turns)"
    elif args.no_synthetic_turns:
        synthesize_turns, turn_mode = False, "forced off (--no-synthetic-turns)"
    elif assert_recovers_input_messages():
        synthesize_turns = False
        turn_mode = "off (ASSERT recovers role=user natively)"
    else:
        synthesize_turns = True
        turn_mode = "on (ASSERT's OTel parser cannot emit role=user; using the TOOL-span workaround)"

    if args.api:
        if not args.public_key or not args.secret_key:
            print(
                "ERROR: --api needs LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY "
                "(or --public-key / --secret-key).",
                file=sys.stderr,
            )
            return 2
        client = LangfuseClient(args.host, args.public_key, args.secret_key)
        print(f"Fetching up to {args.limit} traces from {args.host} ...")
        try:
            traces = fetch_traces(
                client,
                limit=args.limit,
                session_id=args.session_id,
                user_id=args.user_id,
                tags=args.tags,
                from_timestamp=args.from_timestamp,
            )
        except urllib.error.URLError as exc:
            print(f"ERROR: could not reach Langfuse at {args.host}: {exc}", file=sys.stderr)
            return 1
        if not traces:
            print("ERROR: Langfuse returned 0 traces. Nothing converted.", file=sys.stderr)
            return 1
    else:
        traces = load_traces_from_file(args.input)

    otlp = convert_traces(
        traces,
        keep_native_convention=not args.force_synthesize,
        synthesize_turns=synthesize_turns,
    )
    spans = otlp["resourceSpans"][0]["scopeSpans"][0]["spans"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(otlp, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Converted {len(traces)} Langfuse trace(s) -> {len(spans)} span(s)")
    print(f"Synthetic user/system turn spans: {turn_mode}")
    print(f"Wrote OTLP JSON: {args.output}")

    if args.emit_inference_set:
        rows = emit_inference_set(
            args.output,
            args.emit_inference_set,
            group_by=args.group_by,
            behavior=args.behavior,
        )
        print(f"Wrote {len(rows)} inference row(s): {args.emit_inference_set}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
