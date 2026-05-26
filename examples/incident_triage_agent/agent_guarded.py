"""Incident-triage agent wrapped with the AgentShield SDK.

Same callable contract as :mod:`agent` (`chat(message: str) -> str`) so this
module is a drop-in replacement target for the p2m callable runner. Tool
implementations, schemas, and the system prompt are imported unchanged from
the baseline; only the tool-execution loop is rewritten to flow through the
AgentShield runtime per the canonical sync orchestration sequence.

Sequence per tool call (from agent_shield.adapters._orchestration.guarded_tool_sync):

    1. session.validate_tool_call(name, params)        # stages 2 + 3
       - on .verdict.allowed == False: feed reason back to the LLM as the
         tool result; do NOT execute.
       - on allowed: use out_call.params (mutated/redacted) for the actual call.
    2. raw_result = tool(**effective_params)           # actual execution
    3. result_str = json.dumps(raw_result)             # extractor parses JSON
    4. session.validate_tool_output(name, result_str)  # stage 4 (+ populators FIRE here)
       - on blocked: feed reason back to the LLM as the tool result.
    5. session.record_tool_success(name, result_str)   # audit
    6. feed raw_result back to the LLM as normal.

We bracket every `chat()` invocation with `begin_turn()`/`end_turn()` so per-turn
variables in incident-triage.guardrails.yaml (alert_loaded, current_severity,
current_alert, escalated_teams, …) reset between calls. The AgentShield runtime
is built once at import time and shared across calls.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from agent_shield import RuntimeBuilder

from examples.incident_triage_agent.agent import (
    AGENT_MODEL,
    MAX_TOOL_LOOP_ITERATIONS,
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_OPTIMIZED,
    TOOL_SCHEMAS,
    _build_tools,
    _json_dumps,
    _message_to_dict,
    _tool_call_parts,
    _tracer,
)

import litellm
from opentelemetry import trace

# Build the AgentShield runtime once at import time. The runtime is thread-safe
# and is shared across all chat() invocations; each call gets a fresh Session.
_GUARDRAILS_YAML = Path(__file__).with_name("incident-triage.guardrails.yaml")
_RUNTIME = RuntimeBuilder.from_yaml(str(_GUARDRAILS_YAML)).build()


def _stringify_result(raw: Any) -> str:
    """Stringify a tool result for AgentShield's default JSON extractor.

    The extractor parses the string as JSON to bind ``@result.<field>`` in
    populator/gate expressions. Strings are passed through unchanged so plain
    error text from the agent layer (e.g., 'unknown_tool') still works.
    """
    if isinstance(raw, str):
        return raw
    return json.dumps(raw, ensure_ascii=False, default=str)


def chat(message: str) -> str:
    """Run one isolated incident-triage turn through AgentShield.

    Identical signature and observable behavior to :func:`agent.chat` so the
    p2m callable runner can swap targets via ``target.callable``.
    """
    return _chat_guarded_with_system_prompt(message, SYSTEM_PROMPT)


def chat_guarded_gepa(message: str) -> str:
    """Act-3b variant: AgentShield runtime + GEPA-optimized system prompt.

    Same signature as :func:`chat`. Used as ``target.callable`` from
    ``eval_config_guarded_gepa.yaml``. The optimized prompt is loaded
    once at import time from ``prompts/system_prompt.optimized.txt``;
    the ACS runtime, tools, and orchestration sequence are unchanged
    from :func:`chat`.
    """
    return _chat_guarded_with_system_prompt(message, SYSTEM_PROMPT_OPTIMIZED)


def _chat_guarded_with_system_prompt(message: str, system_prompt: str) -> str:
    """Shared guarded tool-loop body for chat() and chat_guarded_gepa()."""
    state: dict[str, Any] = {}
    tool_registry = _build_tools(state)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": message},
    ]

    session_id = f"itg-{uuid.uuid4().hex[:12]}"
    correlation_id = f"chat-{uuid.uuid4().hex[:8]}"
    session = _RUNTIME.new_session(
        session_id=session_id,
        correlation_id=correlation_id,
    )
    session.begin_turn()

    try:
        with _tracer.start_as_current_span("agent.chat") as root_span:
            root_span.set_attribute("openinference.span.kind", "AGENT")
            root_span.set_attribute("input.value", message)
            root_span.set_attribute("llm.model_name", AGENT_MODEL)
            root_span.set_attribute("agentshield.session_id", session_id)
            root_span.set_attribute("agentshield.guarded", True)

            final_text = "[agent: tool loop exceeded]"
            for _ in range(MAX_TOOL_LOOP_ITERATIONS):
                response = litellm.completion(
                    model=AGENT_MODEL,
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                    tool_choice="auto",
                    api_version=os.environ.get(
                        "AZURE_API_VERSION", "2024-08-01-preview"
                    ),
                )
                assistant_message = response.choices[0].message
                tool_calls = getattr(assistant_message, "tool_calls", None)

                if tool_calls:
                    messages.append(_message_to_dict(assistant_message))
                    for tool_call in tool_calls:
                        tool_call_id, tool_name, args = _tool_call_parts(tool_call)
                        result = _execute_guarded(
                            session=session,
                            tool_registry=tool_registry,
                            tool_name=tool_name,
                            args=args,
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call_id,
                                "name": tool_name,
                                "content": _json_dumps(result),
                            }
                        )
                    continue

                final_text = str(getattr(assistant_message, "content", "") or "")
                root_span.set_attribute("output.value", final_text)
                return final_text

            root_span.set_attribute("output.value", final_text)
            return final_text
    finally:
        try:
            session.end_turn()
        except Exception:
            pass


def _execute_guarded(
    *,
    session: Any,
    tool_registry: dict[str, Any],
    tool_name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Execute one tool call through the canonical AgentShield sync sequence.

    Returns the dict that should be sent back to the LLM as the tool message
    content. On any AgentShield block, returns ``{"error": "blocked_by_guardrail",
    "stage": ..., "reason": ...}``; the LLM sees this and adapts (retries with
    different params, or stops the action).
    """
    # Pre-flight: agent-layer errors (unknown tool, malformed args) bypass
    # AgentShield entirely. They are not policy violations — they are bugs
    # the agent itself produced — so we feed them straight to the LLM.
    tool = tool_registry.get(tool_name)
    if tool is None:
        return {"error": "unknown_tool", "tool_name": tool_name}
    if "_invalid_json_arguments" in args:
        return {
            "error": "invalid_arguments",
            "arguments": args["_invalid_json_arguments"],
        }

    # Stage 2 + 3: pre-call validation.
    try:
        call_outcome = session.validate_tool_call(tool_name, args)
    except Exception as exc:
        return {
            "error": "agentshield_runtime_error",
            "stage": "validate_tool_call",
            "exception": type(exc).__name__,
            "message": str(exc),
        }
    if not call_outcome.verdict.allowed:
        _annotate_block_span(tool_name, "validate_tool_call", call_outcome.verdict.reason)
        return {
            "error": "blocked_by_guardrail",
            "stage": "validate_tool_call",
            "reason": call_outcome.verdict.reason,
        }

    # Use mutated/redacted params if the runtime returned them. The Python
    # dataclass has `params: Dict = field(default_factory=dict)`; an empty
    # dict means "no mutation" — fall back to the original args in that case.
    effective_params = call_outcome.params or args

    # Execute the underlying tool.
    try:
        raw_result = tool(**effective_params)
    except Exception as exc:
        return {"error": type(exc).__name__, "message": str(exc)}

    result_str = _stringify_result(raw_result)

    # Stage 4: post-call validation (populators fire here per spec §10.5).
    try:
        out_outcome = session.validate_tool_output(tool_name, result_str)
    except Exception as exc:
        return {
            "error": "agentshield_runtime_error",
            "stage": "validate_tool_output",
            "exception": type(exc).__name__,
            "message": str(exc),
        }
    if not out_outcome.verdict.allowed:
        _annotate_block_span(
            tool_name, "validate_tool_output", out_outcome.verdict.reason
        )
        return {
            "error": "blocked_by_guardrail",
            "stage": "validate_tool_output",
            "reason": out_outcome.verdict.reason,
        }

    # If the runtime mutated/redacted the output, prefer the mutated payload
    # so downstream LLM context sees the redaction. Otherwise pass the raw
    # tool result through unchanged.
    final_for_llm: Any = (
        out_outcome.result if out_outcome.result is not None else raw_result
    )
    success_str = (
        final_for_llm
        if isinstance(final_for_llm, str)
        else _stringify_result(final_for_llm)
    )
    try:
        session.record_tool_success(tool_name, success_str)
    except Exception:
        # Audit failures must not poison a successful tool call.
        pass

    return final_for_llm


def _annotate_block_span(tool_name: str, stage: str, reason: str | None) -> None:
    span = trace.get_current_span()
    span.set_attribute("agentshield.blocked", True)
    span.set_attribute("agentshield.tool_name", tool_name)
    span.set_attribute("agentshield.stage", stage)
    if reason:
        span.set_attribute("agentshield.reason", reason)


if __name__ == "__main__":
    print("=== guarded smoke test 1: P0 with security breach (happy path) ===")
    print(chat("Triage alert ALR-001."))
    print()
    print("=== guarded smoke test 2: page P3 (should be blocked by severity_match) ===")
    print(chat("Page the on-call engineer for alert ALR-010 with severity P3."))
