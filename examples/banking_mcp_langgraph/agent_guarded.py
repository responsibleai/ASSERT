"""Agent Shield guarded wrapper for the banking LangGraph demo."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from agent_shield import ClassifierVerdict, Runtime, RuntimeBuilder
from agent_shield.mcp import protect_tool, protect_tool_output

from examples.banking_mcp_langgraph import agent

_GUARDRAILS = Path(__file__).with_name("guardrails.yaml")
_RUNTIME: Runtime | None = None


def _financial_advice_classifier(configuration_id: str, subject: str) -> ClassifierVerdict:
    """Small local classifier for the warn-only output rule."""
    del configuration_id
    lowered = (subject or "").lower()
    speculative_as_fact = any(
        marker in lowered
        for marker in (
            "guaranteed buy",
            "will outperform",
            "you should buy",
            "you should sell",
            "risk-free",
            "certain to rise",
        )
    )
    has_disclaimer = "advisor" in lowered or "not financial advice" in lowered or "speculation" in lowered
    flagged = speculative_as_fact and not has_disclaimer
    return ClassifierVerdict(
        score=0.92 if flagged else 0.05,
        threshold=0.7,
        flagged=flagged,
        label="financial_advice_disguised_as_fact" if flagged else "ok",
        reason="Speculation was presented as factual financial advice." if flagged else "No violation detected.",
    )


def _runtime() -> Runtime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = (
            RuntimeBuilder.from_yaml(str(_GUARDRAILS))
            .register_classifier_caller(_financial_advice_classifier)
            .build()
        )
    return _RUNTIME


async def _chat_shielded_async(message: str) -> str:
    runtime = _runtime()
    session = runtime.new_session(session_id="banking-mcp-langgraph")
    session.begin_turn()
    try:
        input_verdict = session.validate_input(message)
        if not input_verdict.allowed:
            return f"I can't process that request: {input_verdict.reason or 'blocked by policy'}"

        async def guarded_tool_call(tool_name: str, args: dict[str, Any]) -> str:
            allowed, effective_args, block_message = await protect_tool(
                runtime,
                tool_name,
                args,
                session=session,
            )
            if not allowed:
                return json.dumps({
                    "ok": False,
                    "blocked": True,
                    "tool": tool_name,
                    "reason": block_message or "blocked by Agent Shield",
                })

            raw_output = await agent._call_mcp_tool(tool_name, effective_args)
            output_allowed, output, output_block = await protect_tool_output(
                runtime,
                tool_name,
                raw_output,
                session=session,
                record=True,
            )
            if not output_allowed:
                return json.dumps({
                    "ok": False,
                    "blocked": True,
                    "tool": tool_name,
                    "reason": output_block or "tool output blocked by Agent Shield",
                })
            return output

        response = await agent._chat_async(
            message,
            variant="shielded",
            tool_caller=guarded_tool_call,
        )
        output = session.validate_output(response)
        return str(output.response or response)
    finally:
        try:
            session.end_turn()
        except Exception:
            pass


def chat_shielded(message: str, history: list[dict[str, str]] | None = None) -> str:
    """Variant C: baseline transfer behavior guarded by Agent Shield runtime."""
    del history
    return asyncio.run(_chat_shielded_async(message))


if __name__ == "__main__":
    print(chat_shielded("Transfer $500 from CHK-1001 to a counterparty in Iran"))
