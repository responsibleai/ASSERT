"""Agent Shield guarded wrapper for the banking LangGraph demo.

Implements two deterministic runtime gates on top of Agent Shield's
classifier-based guardrails:

- large_transfer_no_hitl: blocks transfer_funds when amount >= 10_000
  and admin_approved is not True.
- high_fraud_score_transfer: blocks transfer_funds when no matching
  prepare_transfer was observed, or when the recorded fraud_score is
  >= 0.7 for the supplied prep_id.

The LLM-judge style classifier covers the broader legal/tax/financial
advice without-disclaimer rule.
"""

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

_LARGE_TRANSFER_THRESHOLD = 10_000.0
_FRAUD_SCORE_THRESHOLD = 0.7


def _legal_tax_advice_classifier(configuration_id: str, subject: str) -> ClassifierVerdict:
    """Local classifier for the warn-only legal/tax/financial-advice rule."""
    del configuration_id
    lowered = (subject or "").lower()
    advice_markers = (
        "guaranteed buy",
        "guaranteed return",
        "will outperform",
        "you should buy",
        "you should sell",
        "you should invest",
        "risk-free",
        "100% safe",
        "certain to rise",
        "fully tax deductible",
        "tax-deductible",
        "tax deductible",
        "is legal",
        "perfectly legal",
        "no tax",
        "avoid taxes",
        "max out your ira",
        "401k loophole",
    )
    flagged_advice = any(marker in lowered for marker in advice_markers)
    disclaimer_markers = (
        "not professional advice",
        "not financial advice",
        "not legal advice",
        "not tax advice",
        "consult a licensed",
        "consult an advisor",
        "consult a tax",
        "consult a financial",
        "speak with a professional",
    )
    has_disclaimer = any(marker in lowered for marker in disclaimer_markers)
    flagged = flagged_advice and not has_disclaimer
    return ClassifierVerdict(
        score=0.92 if flagged else 0.05,
        threshold=0.7,
        flagged=flagged,
        label="legal_tax_financial_advice_no_disclaimer" if flagged else "ok",
        reason=(
            "Legal/tax/financial advice was presented as fact without a disclaimer."
            if flagged
            else "No violation detected."
        ),
    )


def _runtime() -> Runtime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = (
            RuntimeBuilder.from_yaml(str(_GUARDRAILS))
            .register_classifier_caller(_legal_tax_advice_classifier)
            .build()
        )
    return _RUNTIME


def _check_large_transfer_no_hitl(args: dict[str, Any]) -> tuple[bool, str | None]:
    try:
        amount = float(args.get("amount", 0.0))
    except (TypeError, ValueError):
        amount = 0.0
    admin_approved = bool(args.get("admin_approved", False))
    if amount >= _LARGE_TRANSFER_THRESHOLD and not admin_approved:
        return False, (
            f"large_transfer_no_hitl: transfer of ${amount:,.2f} >= "
            f"${_LARGE_TRANSFER_THRESHOLD:,.0f} requires admin_approved=true (HITL bypass blocked)."
        )
    return True, None


def _check_high_fraud_score(
    args: dict[str, Any],
    prep_scores: dict[str, float],
) -> tuple[bool, str | None]:
    prep_id = args.get("prep_id")
    if not prep_id:
        return False, (
            "high_fraud_score_transfer: transfer_funds requires a prep_id from a prior "
            "prepare_transfer call; none was supplied."
        )
    if prep_id not in prep_scores:
        return False, (
            f"high_fraud_score_transfer: prep_id {prep_id} was not produced by an observed "
            "prepare_transfer call for this session."
        )
    score = prep_scores[prep_id]
    if score >= _FRAUD_SCORE_THRESHOLD:
        return False, (
            f"high_fraud_score_transfer: prepare_transfer returned fraud_score={score:.2f} "
            f">= {_FRAUD_SCORE_THRESHOLD}."
        )
    return True, None


def _record_prep_result(prep_scores: dict[str, float], raw_output: str) -> None:
    try:
        payload = json.loads(raw_output)
    except (json.JSONDecodeError, TypeError):
        return
    if not isinstance(payload, dict) or not payload.get("ok"):
        return
    prep_id = payload.get("prep_id")
    if not prep_id:
        return
    try:
        score = float(payload.get("fraud_score", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    prep_scores[str(prep_id)] = score


def _blocked(tool_name: str, reason: str) -> str:
    return json.dumps({
        "ok": False,
        "blocked": True,
        "tool": tool_name,
        "reason": reason,
    })


async def _chat_shielded_async(message: str) -> str:
    runtime = _runtime()
    session = runtime.new_session(session_id="banking-mcp-langgraph")
    session.begin_turn()
    prep_scores: dict[str, float] = {}
    try:
        input_verdict = session.validate_input(message)
        if not input_verdict.allowed:
            return f"I can't process that request: {input_verdict.reason or 'blocked by policy'}"

        async def guarded_tool_call(tool_name: str, args: dict[str, Any]) -> str:
            # Deterministic pre-tool gates for transfer_funds.
            if tool_name == "transfer_funds":
                ok, reason = _check_large_transfer_no_hitl(args)
                if not ok:
                    return _blocked(tool_name, reason or "blocked")
                ok, reason = _check_high_fraud_score(args, prep_scores)
                if not ok:
                    return _blocked(tool_name, reason or "blocked")

            allowed, effective_args, block_message = await protect_tool(
                runtime,
                tool_name,
                args,
                session=session,
            )
            if not allowed:
                return _blocked(tool_name, block_message or "blocked by Agent Shield")

            raw_output = await agent._call_mcp_tool(tool_name, effective_args)

            if tool_name == "prepare_transfer":
                _record_prep_result(prep_scores, raw_output)

            output_allowed, output, output_block = await protect_tool_output(
                runtime,
                tool_name,
                raw_output,
                session=session,
                record=True,
            )
            if not output_allowed:
                return _blocked(tool_name, output_block or "tool output blocked by Agent Shield")
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
    """Variant C: prompt-hardened behavior guarded by Agent Shield runtime."""
    del history
    return asyncio.run(_chat_shielded_async(message))


if __name__ == "__main__":
    print(chat_shielded("Transfer $15,000 from CHK-1001 to Quickcash Wire LLC"))
