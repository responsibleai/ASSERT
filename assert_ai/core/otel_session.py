# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""OTel-traced session for multi-turn adversarial evaluation.

Architecture:
    Turn 1: Tester generates probe → CallableSession invokes target →
            OTel captures agent internals → ASSERT records turn + trace metadata
    Turn 2: Tester escalates based on Turn 1 response → same flow
    ...
    Turn N: Max turns reached or stop condition met

    After all turns: Full trace tree available for judge with per-node,
    per-tool, per-LLM-call visibility.

This differs from Phoenix's approach (collect-then-evaluate) because ASSERT
actively DRIVES the conversation with adversarial probing while simultaneously
capturing the target's internal execution traces.
"""

from __future__ import annotations

import importlib
import inspect
import uuid
from contextlib import nullcontext
from typing import Any

from assert_ai.core.async_utils import invoke_callable
from assert_ai.core.collector import SpanCollector
from assert_ai.core.model_client import Message
from assert_ai.core.otel import (
    InMemoryTraceExporter,
    OTelSpan,
    TraceExporter,
    _spans_to_events,
    compress_trace_for_judge,
    validate_spans,
)
from assert_ai.core.session import TurnResult


class OTelTracedSession:
    """Session that invokes a callable target and captures OTel traces per turn.

    Supports two modes:
    1. Auto-instrumented: Target is already instrumented with OpenInference.
       Traces are collected from an OTel collector (Phoenix, Jaeger, file).
    2. In-memory: For testing. Spans are injected directly via add_span().

    Multi-turn flow:
        ASSERT's inference stage drives the conversation. Each turn:
        1. Tester generates the next adversarial message
        2. This session invokes the target callable
        3. Target executes (emitting OTel spans if instrumented)
        4. Session captures response + collects trace data for this turn
        5. Returns TurnResult with rich trace metadata in ``raw``

        The judge receives the FULL conversation history with per-turn
        trace metadata — every node visited, tool called, and LLM invocation.

    The ``collector`` parameter accepts any :class:`SpanCollector` — the
    preferred, Protocol-based interface.  ``exporter`` (the older
    :class:`TraceExporter` interface) is still supported for backward
    compatibility.
    """

    def __init__(
        self,
        *,
        callable_ref: str,
        exporter: TraceExporter | None = None,
        collector: SpanCollector | None = None,
        group_by: str = "session.id",
        system_prompt: str | None = None,
        message_timeout_s: float | None = None,
        max_events_per_turn: int = 50,
        live_otel: bool = False,
    ) -> None:
        self._callable_ref = callable_ref
        self._collector = collector
        self._group_by = group_by
        self._system_prompt = system_prompt
        self._message_timeout_s = message_timeout_s
        self._max_events_per_turn = max_events_per_turn
        self._callable: Any = None
        self._supports_history = False
        self._session_id = ""
        self._turn_traces: list[dict[str, Any]] = []
        self._live_otel = live_otel

        if live_otel:
            from assert_ai.core.otel import LiveOTelExporter
            self._live_exporter = LiveOTelExporter()
            self._exporter = self._live_exporter
        else:
            self._live_exporter = None
            self._exporter = exporter or InMemoryTraceExporter()

    @property
    def runtime_mode(self) -> str:
        return "otel_traced"

    @property
    def session_metadata(self) -> dict[str, Any] | None:
        return {
            "session_id": self._session_id,
            "trace_backend": "otel",
            "turn_count": len(self._turn_traces),
        }

    async def open(self) -> None:
        import io
        import sys

        from assert_ai.core.security import validate_callable_ref

        validate_callable_ref(self._callable_ref)
        module_path, func_name = self._callable_ref.rsplit(":", 1)
        # Suppress Phoenix/OTel banner output during module import.
        # Phoenix's register(verbose=True) prints a multi-line banner to
        # stdout when the target module calls register() at import time.
        _orig_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            mod = importlib.import_module(module_path)
        except ModuleNotFoundError as exc:
            raise ValueError(
                f"Could not import module '{module_path}' from callable ref "
                f"'{self._callable_ref}'. Is the package installed? ({exc})"
            ) from exc
        finally:
            sys.stdout = _orig_stdout
        try:
            self._callable = getattr(mod, func_name)
        except AttributeError as exc:
            raise ValueError(
                f"Module '{module_path}' has no attribute '{func_name}'. "
                f"Check your callable reference '{self._callable_ref}'."
            ) from exc
        sig = inspect.signature(self._callable)
        self._supports_history = "history" in sig.parameters
        self._session_id = uuid.uuid4().hex[:12]
        self._turn_traces = []
        if self._live_exporter is not None:
            self._live_exporter.setup()

    async def close(self) -> None:
        self._callable = None
        self._turn_traces = []

    async def run_turn(self, messages: list[Message]) -> TurnResult:
        """Execute one turn: invoke target, capture traces, return rich result.

        The ``group_by`` setting controls trace granularity:
        - ``session.id``: All spans → one aggregate (full conversation context)
        - ``trace.id``: Spans grouped by trace_id → per-trace events
        - ``span.id``: Each span → its own event (maximum granularity)

        When using ``LiveOTelExporter`` (singleton, shared span buffer), the
        clear-invoke-export cycle is serialized via an ``asyncio.Lock`` to
        prevent concurrent sessions from contaminating each other's span
        data. The lock MUST be ``asyncio.Lock`` (not ``threading.Lock``):
        the inference stage runs all sessions in one event loop, so holding a
        sync threading lock across the inner ``await`` would block the loop
        and deadlock when ``inference.concurrency > 1``.
        """
        from assert_ai.core.otel import LiveOTelExporter
        lock_ctx = LiveOTelExporter.get_lock() if self._live_otel else nullcontext()
        async with lock_ctx:
            # Clear spans from previous turn so we only capture this turn's execution
            if self._live_exporter is not None:
                self._live_exporter.clear()

            user_text = ""
            for msg in reversed(messages):
                if msg.role == "user":
                    user_text = msg.text
                    break

            turn_id = f"{self._session_id}_turn_{len(self._turn_traces)}"

            # Invoke the callable (which triggers the instrumented agent)
            if self._supports_history:
                history = [
                    {"role": msg.role, "content": msg.text}
                    for msg in messages
                    if msg.role in ("user", "assistant")
                ]
                response_text = await invoke_callable(
                    self._callable,
                    user_text,
                    history=history,
                    timeout_s=self._message_timeout_s,
                )
            else:
                response_text = await invoke_callable(
                    self._callable,
                    user_text,
                    timeout_s=self._message_timeout_s,
                )

            if not isinstance(response_text, str):
                response_text = str(response_text)

            # Collect traces for this turn
            turn_spans = self._exporter.export_session(turn_id)

        validation = validate_spans(turn_spans)

        # Convert spans to events (tool call visibility + judge metadata)
        if turn_spans:
            all_conversation_events, full_aggregate = _spans_to_events(turn_spans)
            all_conversation_events = compress_trace_for_judge(
                all_conversation_events,
                max_events=self._max_events_per_turn,
            )
        else:
            all_conversation_events = []
            full_aggregate = {
                "nodes_visited": [],
                "tools_called": [],
                "total_tokens": {"input": 0, "output": 0},
                "total_latency_ms": 0.0,
                "llm_call_count": 0,
            }

        # Record turn trace data
        turn_trace = {
            "turn_id": turn_id,
            "turn_index": len(self._turn_traces),
            "events": all_conversation_events,
            "aggregate": full_aggregate,
            "validation": {
                "valid": validation.valid,
                "warnings": validation.warnings,
            },
        }
        self._turn_traces.append(turn_trace)

        # Build interaction messages — always uses ALL spans for tool visibility
        interaction_messages: list[dict[str, Any]] = [
            {"role": "user", "content": user_text},
        ]

        for event in all_conversation_events:
            if event.get("actor") == "tool":
                edit = event.get("edit", {})
                interaction_messages.append({
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"tc_{edit.get('tool_name', 'unknown')}",
                            "function": edit.get("tool_name", ""),
                            "arguments": edit.get("tool_args", {}),
                        }
                    ],
                })
                interaction_messages.append({
                    "role": "tool",
                    "content": edit.get("tool_result", ""),
                    "function": edit.get("tool_name", ""),
                    "tool_call_id": f"tc_{edit.get('tool_name', 'unknown')}",
                })

        interaction_messages.append({
            "role": "assistant",
            "content": response_text,
            "raw": {
                "trace_events": all_conversation_events,
                "trace_metadata": full_aggregate,
            },
        })

        return TurnResult(
            text=response_text,
            state_messages=list(messages) + [Message(role="assistant", content=response_text)],
            interaction_messages=interaction_messages,
            raw={
                "session_id": self._session_id,
                "turn_id": turn_id,
                "runtime_mode": "otel_traced",
                "trace_events": all_conversation_events,
                "trace_metadata": full_aggregate,
                "span_validation": turn_trace["validation"],
                "accumulated_turns": len(self._turn_traces),
            },
        )
