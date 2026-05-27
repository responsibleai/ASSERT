# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""SpanCollector Protocol — decouples P2M from any specific trace backend.

P2M's OTel integration depends on this Protocol, not on Phoenix.
Phoenix is one implementation. Developers can inject any backend.

The canonical span type is OTelSpan (from p2m.core.otel) — JSON-native,
no pandas dependency in the critical path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from p2m.core.otel import OTelSpan

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
    """Minimal interface P2M depends on for trace collection.

    Returns list[OTelSpan] — JSON-native, no pandas dependency.
    Any object implementing get_spans() satisfies this — no inheritance needed.
    Phoenix is one implementation. Jaeger/Datadog/file export are others.
    """

    def get_spans(
        self,
        project_name: str,
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
    Install: pip install 'p2m-taxonomy[otel]'

    Queries Phoenix for DataFrame, then converts to list[OTelSpan] internally.
    """

    def __init__(
        self,
        endpoint: str = "http://localhost:6006",
        *,
        project_name: str | None = None,
    ) -> None:
        try:
            import phoenix as px
            self._client = px.Client(endpoint=endpoint)
        except ImportError as e:
            raise ImportError(
                "PhoenixCollector requires arize-phoenix. "
                "Install with: pip install 'p2m-taxonomy[otel]'"
            ) from e
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

        try:
            df: pd.DataFrame = self._client.get_spans_dataframe(
                project_name=name,
                start_time=start_time,
                end_time=end_time,
            )
        except ConnectionError as exc:
            raise RuntimeError(
                f"Cannot connect to Phoenix at {self._client._base_url if hasattr(self._client, '_base_url') else 'unknown'} "
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


def _dataframe_to_otel_spans(df: Any) -> list[Any]:
    """Convert an OpenInference-format DataFrame to list[OTelSpan].

    Imports OTelSpan lazily to avoid circular imports at module load.
    """
    from p2m.core.otel import OTelSpan

    spans = []
    for _, row in df.iterrows():
        attrs: dict[str, Any] = {}
        for col in df.columns:
            if col.startswith("attributes."):
                key = col[len("attributes."):]
                val = row[col]
                if val is not None and not (isinstance(val, float) and val != val):
                    attrs[key] = val

        spans.append(OTelSpan(
            trace_id=str(row.get("context.trace_id", "")),
            span_id=str(row.get("context.span_id", "")),
            parent_span_id=str(row["parent_id"]) if row.get("parent_id") else None,
            name=str(row.get("name", "")),
            kind=attrs.get("openinference.span.kind", "UNKNOWN"),
            start_time_ns=int(row.get("start_time", 0)) if row.get("start_time") else 0,
            end_time_ns=int(row.get("end_time", 0)) if row.get("end_time") else 0,
            attributes=attrs,
        ))
    return spans
