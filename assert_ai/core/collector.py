# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""SpanCollector Protocol — decouples ASSERT from any specific trace backend.

ASSERT's OTel integration depends on this Protocol, not on Phoenix.
Phoenix is one implementation. Developers can inject any backend.

The canonical span type is OTelSpan (from assert_ai.core.otel) — JSON-native,
no pandas dependency in the critical path.
"""

from __future__ import annotations

import heapq
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from numbers import Integral, Real
from sys import maxsize as _PHOENIX_RAW_QUERY_LIMIT
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from assert_ai.core.otel import OTelSpan

# OpenInference attribute keys for validation
REQUIRED_ATTRIBUTES = frozenset({
    "openinference.span.kind",
})

RECOMMENDED_LLM_ATTRIBUTES = frozenset({
    "llm.model_name",
    "llm.token_count.prompt",
    "llm.token_count.completion",
    "output.value",
})

# Legacy DataFrame clients cannot express an unbounded query; use the largest
# portable GraphQL Int while keeping the supported raw API genuinely cursor-complete.
_PHOENIX_LEGACY_QUERY_LIMIT = 2_147_483_647


@runtime_checkable
class SpanCollector(Protocol):
    """Minimal interface ASSERT depends on for trace collection.

    Returns list[OTelSpan] — JSON-native, no pandas dependency.
    Any object implementing get_spans() satisfies this — no inheritance needed.
    Phoenix is one implementation. Jaeger/Datadog/file export are others.
    """

    def get_spans(
        self,
        project_name: str | None = None,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        trace_ids: list[str] | None = None,
    ) -> list[OTelSpan]:
        """Return spans as OTelSpan objects."""
        ...

    def validate(self, spans: list[OTelSpan]) -> list[str]:
        """Return warnings for missing/malformed attributes. Empty = OK."""
        ...


def _validate_otel_spans(spans: list[Any]) -> list[str]:
    """Shared validation logic for OTelSpan lists."""
    warnings: list[str] = []
    llm_missing_output = 0
    has_session_id = False

    for span in spans:
        if span.kind == "UNKNOWN":
            warnings.append(f"span {span.span_id}: missing openinference.span.kind")
        if span.kind == "LLM" and not span.attributes.get("output.value"):
            llm_missing_output += 1
        if span.attributes.get("session.id"):
            has_session_id = True

    if llm_missing_output > 0:
        warnings.append(
            f"{llm_missing_output} LLM span(s) missing output.value. "
            "Trajectory evaluation will be incomplete."
        )
    if not has_session_id and spans:
        warnings.append("No session.id attribute. Session-level evaluation requires this.")

    return warnings


class ListCollector:
    """Wraps a pre-loaded list of OTelSpan objects as a SpanCollector.

    Use when you already have spans from any source — file export,
    in-memory test fixtures, or any converter output.
    """

    def __init__(self, spans: list[Any]) -> None:
        self._spans = list(spans)

    def get_spans(self, project_name: str | None = None, **kwargs: Any) -> list[Any]:
        return self._spans

    def validate(self, spans: list[Any]) -> list[str]:
        return _validate_otel_spans(spans)


class DataFrameCollector:
    """Wraps a pre-loaded DataFrame as a SpanCollector.

    Converts OpenInference DataFrame rows → OTelSpan objects on get_spans().
    Use when you have spans from Arize cloud export, Parquet file, or similar.
    """

    def __init__(self, df: Any) -> None:
        self._df = df

    def get_spans(self, project_name: str | None = None, **kwargs: Any) -> list[Any]:
        return _dataframe_to_otel_spans(self._df)

    def validate(self, spans: list[Any]) -> list[str]:
        return _validate_otel_spans(spans)


class PhoenixCollector:
    """SpanCollector backed by a local Phoenix instance.

    Phoenix is an OPTIONAL dependency — only imported when instantiated.
    Install: pip install 'assert-ai[phoenix]'

    Uses Phoenix's event-bearing span API and converts to list[OTelSpan].
    """

    def __init__(
        self,
        endpoint: str = "http://localhost:6006",
        *,
        project_name: str | None = None,
    ) -> None:
        try:
            from phoenix.client import Client  # type: ignore[import-not-found]

            self._client = Client(base_url=endpoint)
        except ImportError as e:
            raise ImportError(
                "PhoenixCollector requires arize-phoenix. "
                "Install with: pip install 'assert-ai[phoenix]'"
            ) from e
        self._endpoint = endpoint
        self._default_project = project_name

    def get_spans(
        self,
        project_name: str | None = None,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        trace_ids: list[str] | None = None,
    ) -> list[Any]:
        name = project_name or self._default_project
        if name is None:
            raise ValueError("project_name required")
        start_datetime = _parse_datetime(start_time, field_name="start_time")
        end_datetime = _parse_datetime(end_time, field_name="end_time")

        try:
            span_client = self._client.spans
            raw_get_spans = getattr(span_client, "get_spans", None)
            if callable(raw_get_spans):
                query: dict[str, Any] = {
                    "project_identifier": name,
                    "start_time": start_datetime,
                    "end_time": end_datetime,
                    "limit": _PHOENIX_RAW_QUERY_LIMIT,
                }
                if trace_ids:
                    query["trace_ids"] = list(trace_ids)
                raw_spans = raw_get_spans(**query)
                if not isinstance(raw_spans, Sequence) or isinstance(raw_spans, (str, bytes)):
                    raise TypeError("Phoenix get_spans() returned a non-sequence value")
                return _order_spans([
                    _phoenix_span_to_otel_span(raw_span)
                    for raw_span in raw_spans
                ])

            # Compatibility fallback for clients predating the raw span API.
            # It cannot recover span events, so supported clients are tested on
            # the raw path and this remains only a bounded legacy escape hatch.
            import pandas as pd  # type: ignore[import-not-found]

            df: pd.DataFrame = span_client.get_spans_dataframe(
                project_identifier=name,
                start_time=start_datetime,
                end_time=end_datetime,
                limit=_PHOENIX_LEGACY_QUERY_LIMIT,
            )
            if trace_ids:
                if "context.trace_id" not in df.columns:
                    raise RuntimeError(
                        "Phoenix DataFrame missing 'context.trace_id' column. "
                        f"Available columns: {list(df.columns)}"
                    )
                df = df[df["context.trace_id"].isin(trace_ids)]
            return _order_spans(_dataframe_to_otel_spans(df))
        except ConnectionError as exc:
            raise RuntimeError(
                f"Cannot connect to Phoenix at {self._endpoint} "
                f"for project '{name}': {exc}"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"Failed to fetch spans from Phoenix for project '{name}': "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    def validate(self, spans: list[Any]) -> list[str]:
        return _validate_otel_spans(spans)


def _parse_datetime(value: str | None, *, field_name: str) -> datetime | None:
    """Convert the collector protocol's ISO timestamp to Phoenix's datetime API."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO-8601 string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ValueError(f"{field_name} must include a timezone offset")
    return parsed.astimezone(UTC)


def _is_missing(value: Any) -> bool:
    """Return whether a scalar DataFrame value represents missing data."""
    if value is None:
        return True
    try:
        missing = value != value
        return bool(missing)
    except (TypeError, ValueError):
        try:
            import pandas as pd  # type: ignore[import-not-found]

            return bool(pd.isna(value))
        except (TypeError, ValueError):
            return False


def _timestamp_to_ns(value: Any, *, field_name: str) -> int:
    """Normalize Phoenix numeric or timezone-aware timestamp values to nanoseconds."""
    if _is_missing(value):
        return 0
    native_ns = getattr(value, "value", None)
    if isinstance(native_ns, Integral) and not isinstance(native_ns, bool):
        return int(native_ns)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError(f"{field_name} must be timezone-aware")
        utc_value = value.astimezone(UTC)
        delta = utc_value - datetime(1970, 1, 1, tzinfo=UTC)
        return (
            (delta.days * 86_400 + delta.seconds) * 1_000_000_000
            + delta.microseconds * 1_000
        )
    if isinstance(value, Real) and not isinstance(value, bool):
        return int(value)  # type: ignore[arg-type]
    if isinstance(value, str):
        parsed = _parse_datetime(value, field_name=field_name)
        assert parsed is not None
        return _timestamp_to_ns(parsed, field_name=field_name)
    raise TypeError(f"{field_name} has unsupported type {type(value).__name__}")


def _dataframe_to_otel_spans(df: Any) -> list[Any]:
    """Convert an OpenInference-format DataFrame to list[OTelSpan].

    Imports OTelSpan lazily to avoid circular imports at module load.
    """
    from assert_ai.core.otel import OTelSpan

    spans = []
    for index, row in df.iterrows():
        attrs: dict[str, Any] = {}
        for col in df.columns:
            if col.startswith("attributes."):
                key = col[len("attributes."):]
                val = row[col]
                if not _is_missing(val):
                    attrs[key] = val

        trace_id = row.get("context.trace_id", "")
        span_id = row.get("context.span_id", row.get("span_id", ""))
        if _is_missing(span_id) or span_id == "":
            span_id = index if isinstance(index, str) else ""
        parent_span_id = row.get("parent_id")
        if _is_missing(parent_span_id) or parent_span_id == "":
            parent_span_id = None
        kind = attrs.get("openinference.span.kind")
        if _is_missing(kind) or not kind:
            kind = row.get("span_kind")
        if _is_missing(kind) or not kind:
            kind = "UNKNOWN"

        spans.append(OTelSpan(
            trace_id="" if _is_missing(trace_id) else str(trace_id),
            span_id=str(span_id),
            parent_span_id=str(parent_span_id) if parent_span_id is not None else None,
            name=str(row.get("name", "")),
            kind=str(kind),
            start_time_ns=_timestamp_to_ns(
                row.get("start_time"),
                field_name="start_time",
            ),
            end_time_ns=_timestamp_to_ns(
                row.get("end_time"),
                field_name="end_time",
            ),
            attributes=attrs,
        ))
    return spans


def _phoenix_span_to_otel_span(raw: Mapping[str, Any]) -> "OTelSpan":
    """Convert one event-bearing Phoenix API span to ASSERT's neutral shape."""
    from assert_ai.core.otel import OTelSpan

    context = raw.get("context") or {}
    if not isinstance(context, Mapping):
        raise ValueError("Phoenix span context must be an object")
    raw_attributes = raw.get("attributes") or {}
    if not isinstance(raw_attributes, Mapping):
        raise ValueError("Phoenix span attributes must be an object")
    attributes = dict(raw_attributes)
    return OTelSpan(
        trace_id=str(context.get("trace_id") or ""),
        span_id=str(context.get("span_id") or ""),
        parent_span_id=(str(raw["parent_id"]) if raw.get("parent_id") else None),
        name=str(raw.get("name") or ""),
        kind=str(
            attributes.get("openinference.span.kind")
            or raw.get("span_kind")
            or "UNKNOWN"
        ),
        start_time_ns=_timestamp_to_ns(raw.get("start_time"), field_name="start_time"),
        end_time_ns=_timestamp_to_ns(raw.get("end_time"), field_name="end_time"),
        attributes=attributes,
        status=str(raw.get("status_code") or "OK"),
        events=_phoenix_events_to_otlp(raw.get("events") or []),
    )


def _phoenix_events_to_otlp(raw_events: Any) -> list[dict[str, Any]]:
    """Preserve Phoenix event content in the OTLP-JSON shape ASSERT consumes."""
    if not isinstance(raw_events, Sequence) or isinstance(raw_events, (str, bytes)):
        raise ValueError("Phoenix span events must be a sequence")
    events: list[dict[str, Any]] = []
    for raw_event in raw_events:
        if not isinstance(raw_event, Mapping):
            raise ValueError("Phoenix span event must be an object")
        raw_attributes = raw_event.get("attributes") or {}
        if not isinstance(raw_attributes, Mapping):
            raise ValueError("Phoenix span event attributes must be an object")
        event: dict[str, Any] = {
            "name": str(raw_event.get("name") or ""),
            "attributes": [
                {"key": str(key), "value": _to_otlp_value(value)}
                for key, value in raw_attributes.items()
            ],
        }
        if raw_event.get("timestamp") is not None:
            event["timeUnixNano"] = str(
                _timestamp_to_ns(raw_event["timestamp"], field_name="event timestamp")
            )
        events.append(event)
    return events


def _to_otlp_value(value: Any) -> dict[str, Any]:
    """Encode a Phoenix event attribute using OTLP's JSON value shape."""
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, (list, tuple)):
        return {"arrayValue": {"values": [_to_otlp_value(item) for item in value]}}
    if isinstance(value, Mapping):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return {"stringValue": "" if value is None else str(value)}


def _order_spans(spans: list["OTelSpan"]) -> list["OTelSpan"]:
    """Produce deterministic chronology with parent-first ordering on time ties."""
    groups: defaultdict[tuple[int, str], list["OTelSpan"]] = defaultdict(list)
    for span in spans:
        groups[(span.start_time_ns, span.trace_id)].append(span)

    ordered: list["OTelSpan"] = []
    for group_key in sorted(groups):
        group = groups[group_key]
        by_id = {span.span_id: span for span in group}
        if len(by_id) != len(group):
            raise ValueError(
                f"Phoenix returned duplicate span IDs for trace {group_key[1]!r}"
            )

        children: defaultdict[str, list[str]] = defaultdict(list)
        indegree = {span_id: 0 for span_id in by_id}
        for span in group:
            parent_id = span.parent_span_id
            if parent_id in by_id and parent_id != span.span_id:
                children[parent_id].append(span.span_id)
                indegree[span.span_id] += 1

        pending = set(by_id)
        ready = [span_id for span_id, count in indegree.items() if count == 0]
        heapq.heapify(ready)
        while pending:
            if not ready:
                # Break malformed parent cycles by the stable span ID rather
                # than inheriting Phoenix's page/input order.
                heapq.heappush(ready, min(pending))
            span_id = heapq.heappop(ready)
            if span_id not in pending:
                continue
            pending.remove(span_id)
            ordered.append(by_id[span_id])
            for child_id in sorted(children.get(span_id, [])):
                if child_id not in pending:
                    continue
                indegree[child_id] -= 1
                if indegree[child_id] == 0:
                    heapq.heappush(ready, child_id)

    return ordered
