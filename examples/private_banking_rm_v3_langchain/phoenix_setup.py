"""Phoenix/OpenInference setup for the private-banking RM v3 demo."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

_CONFIGURED = False
_LOCK = threading.Lock()


class _JsonlSpanExporter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def export(self, spans: list[Any]) -> Any:
        from opentelemetry.sdk.trace.export import SpanExportResult

        rows = []
        for span in spans:
            context = getattr(span, "context", None)
            parent = getattr(span, "parent", None)
            rows.append(
                {
                    "name": span.name,
                    "trace_id": f"{context.trace_id:032x}" if context else "",
                    "span_id": f"{context.span_id:016x}" if context else "",
                    "parent_span_id": f"{parent.span_id:016x}" if parent else None,
                    "start_time_unix_nano": span.start_time,
                    "end_time_unix_nano": span.end_time,
                    "attributes": dict(span.attributes or {}),
                    "status": getattr(getattr(span, "status", None), "status_code", None).name
                    if getattr(span, "status", None)
                    else None,
                }
            )
        if rows:
            with self._lock:
                with self.path.open("a", encoding="utf-8") as handle:
                    for row in rows:
                        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


def _endpoint_reachable(endpoint: str) -> bool:
    if not endpoint:
        return False
    probe = endpoint.rstrip("/")
    if probe.endswith("/v1/traces"):
        probe = probe[: -len("/v1/traces")]
    try:
        with urllib.request.urlopen(probe, timeout=0.5):  # noqa: S310 - local Phoenix probe
            return True
    except Exception:
        return False


def _add_file_exporter(tracer_provider: Any) -> None:
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    span_path = Path(os.environ.get("P2M_PHOENIX_SPANS_PATH", "artifacts/phoenix/spans.jsonl"))
    processor = SimpleSpanProcessor(_JsonlSpanExporter(span_path))
    try:
        tracer_provider.add_span_processor(processor, replace_default_processor=False)
    except TypeError:
        tracer_provider.add_span_processor(processor)


def configure() -> Any:
    """Configure Phoenix if reachable and always add a local JSONL span exporter."""
    global _CONFIGURED
    with _LOCK:
        if _CONFIGURED:
            from opentelemetry import trace

            return trace.get_tracer_provider()

        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from openinference.instrumentation.langchain import LangChainInstrumentor

        endpoint = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "").strip()
        project_name = os.environ.get("PHOENIX_PROJECT_NAME", "private-banking-rm-v3")

        if endpoint and _endpoint_reachable(endpoint):
            from phoenix.otel import register

            tracer_provider = register(
                endpoint=endpoint,
                project_name=project_name,
                set_global_tracer_provider=True,
                protocol="http/protobuf",
                batch=False,
                verbose=False,
            )
        else:
            tracer_provider = TracerProvider()
            trace.set_tracer_provider(tracer_provider)

        _add_file_exporter(tracer_provider)
        try:
            LangChainInstrumentor().instrument(tracer_provider=tracer_provider)
        except Exception:
            pass

        _CONFIGURED = True
        return tracer_provider


_TRACER_PROVIDER = configure()
