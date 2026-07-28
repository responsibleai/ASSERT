"""Observability for the retrieval step — see *into* the knowledge lookup.

A RAG/agent failure often hides behind a polished final answer: the generation
was fine, the *inputs* were bad (wrong scope, paraphrase miss, irrelevant high-
overlap hit). Input/output eval can't see that — you have to trace the retrieval
itself. This module emits one record per `KBBackend.retrieve()` call carrying the
query, the citations it surfaced, their scores, the active score threshold, and
the resulting `grounded` verdict.

Two sinks, both no-op-safe when unconfigured so the offline demo path pays
nothing and stays byte-identical:

  - **Structured JSONL** (always the source of truth): appended to the file named
    by `RETRIEVAL_TRACE_PATH`. The retrieval-quality eval and the on-stage
    "open the trace" view both read this — no collector required.
  - **OpenTelemetry span** (best effort): emitted via the base `opentelemetry`
    SDK. With no tracer provider configured `get_tracer` returns a no-op, so this
    is silent locally; on a box running Phoenix (`PHOENIX_COLLECTOR_ENDPOINT`)
    the span shows up as a `RETRIEVER` step nested under the tool call.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def _otel_span(record: dict[str, Any]) -> None:
    """Best-effort base-OTel span. No-op when no provider is configured."""
    try:
        from opentelemetry import trace
    except Exception:
        return
    tracer = trace.get_tracer("bank.kb.retrieval")
    # OpenInference semantic convention for a retriever step, when a UI groks it.
    with tracer.start_as_current_span("knowledge_base.retrieve") as span:
        try:
            span.set_attribute("openinference.span.kind", "RETRIEVER")
            span.set_attribute("retrieval.backend", record.get("backend", ""))
            span.set_attribute("input.value", record.get("query", ""))
            span.set_attribute("retrieval.grounded", bool(record.get("grounded")))
            span.set_attribute("retrieval.n_citations", int(record.get("n_citations", 0)))
            if record.get("top_score") is not None:
                span.set_attribute("retrieval.top_score", float(record["top_score"]))
            if record.get("score_threshold") is not None:
                span.set_attribute("retrieval.score_threshold", float(record["score_threshold"]))
            for i, c in enumerate(record.get("citations", [])):
                span.set_attribute(f"retrieval.documents.{i}.document.id", str(c.get("ref_id", "")))
                span.set_attribute(f"retrieval.documents.{i}.document.score", float(c.get("score", 0.0)))
        except Exception:
            pass


def emit(*, backend: str, query: str, result: dict[str, Any],
         score_threshold: float | None, elapsed_ms: float | None = None) -> dict[str, Any]:
    """Build the retrieval-trace record, write it to the configured sinks, return it."""
    citations = result.get("citations", []) or []
    scores = [c.get("score") for c in citations if c.get("score") is not None]
    record = {
        "ts": round(time.time(), 3),
        "backend": backend,
        "query": query,
        "score_threshold": score_threshold,
        "n_citations": len(citations),
        "top_score": max(scores) if scores else None,
        "grounded": bool(result.get("grounded")),
        "elapsed_ms": round(elapsed_ms, 1) if elapsed_ms is not None else None,
        # trimmed citations (drop the long snippet) so the trace stays scannable
        "citations": [
            {"ref_id": c.get("ref_id"), "source": c.get("source"), "score": c.get("score")}
            for c in citations
        ],
    }

    path = os.environ.get("RETRIEVAL_TRACE_PATH")
    if path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

    _otel_span(record)
    return record
