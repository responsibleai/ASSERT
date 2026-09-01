# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""SpanCollector Protocol — decouples ASSERT from any specific trace backend.

ASSERT's OTel integration depends on this Protocol, not on Phoenix.
Phoenix is one implementation. Developers can inject any backend.

The canonical span type is OTelSpan (from assert_ai.core.otel) — JSON-native,
no pandas dependency in the critical path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from numbers import Integral, Real
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

    Queries Phoenix for DataFrame, then converts to list[OTelSpan] internally.
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
        import pandas as pd

        name = project_name or self._default_project
        if name is None:
            raise ValueError("project_name required")
        start_datetime = _parse_datetime(start_time, field_name="start_time")
        end_datetime = _parse_datetime(end_time, field_name="end_time")

        try:
            df: pd.DataFrame = self._client.spans.get_spans_dataframe(
                project_identifier=name,
                start_time=start_datetime,
                end_time=end_datetime,
            )
        except ConnectionError as exc:
            raise RuntimeError(
                f"Cannot connect to Phoenix at {self._endpoint} "
                f"for project '{name}': {exc}"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"Failed to fetch spans from Phoenix for project '{name}': {type(exc).__name__}: {exc}"
            ) from exc
        if trace_ids:
            if "context.trace_id" not in df.columns:
                raise RuntimeError(
                    "Phoenix DataFrame missing 'context.trace_id' column. "
                    f"Available columns: {list(df.columns)}"
                )
            df = df[df["context.trace_id"].isin(trace_ids)]

        return _dataframe_to_otel_spans(df)

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
        return int(value.timestamp() * 1_000_000_000)
    if isinstance(value, Real) and not isinstance(value, bool):
        return int(value)  # type: ignore[arg-type]
    if isinstance(value, str):
        parsed = _parse_datetime(value, field_name=field_name)
        assert parsed is not None
        return int(parsed.timestamp() * 1_000_000_000)
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
