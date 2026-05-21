from __future__ import annotations

import logging
import os
import re
from typing import Any

from dotenv import load_dotenv

try:
    from . import agent as baseline
except ImportError:  # pragma: no cover - direct script execution
    import agent as baseline  # type: ignore

load_dotenv()
os.environ.setdefault("AZURE_API_VERSION", "2024-08-01-preview")

try:
    import litellm
except Exception:  # pragma: no cover - dependency is optional for local smoke
    litellm = None  # type: ignore

log = logging.getLogger(__name__)

CURRENT_RM_ID = "rm-1042"
AUX_CLASSIFIER_MODEL = os.environ.get("PRIVATE_BANKING_RM_MODEL", baseline.AGENT_MODEL)
_RULE_LOOKUP = "portfolio_lookup_rm_assignment"
_RULE_EMAIL = "email_recipient_domain_allowlist"
_RULE_WIRE = "wire_amount_country_sanctions"
_RULE_AUX = "legal_or_tax_advice_in_email_body"


def _guardrail_span(
    rule_id: str,
    reason: str,
    *,
    blocked: bool = False,
    escalated: bool = False,
    aux_warning: bool = False,
) -> None:
    with baseline._tracer.start_as_current_span(f"agentshield.{rule_id}") as span:
        span.set_attribute("agentshield.rule", rule_id)
        span.set_attribute("agentshield.reason", reason)
        span.set_attribute("agentshield.blocked", blocked)
        span.set_attribute("agentshield.escalated", escalated)
        span.set_attribute("agentshield.aux_warning", aux_warning)


def _blocked(rule_id: str, reason: str) -> str:
    _guardrail_span(rule_id, reason, blocked=True)
    return f"blocked_by_guardrail: {rule_id}. {reason}"


def _escalated(rule_id: str, reason: str) -> str:
    _guardrail_span(rule_id, reason, escalated=True)
    return f"escalated_to_hitl: {rule_id}. {reason}"


def _allowed_client_ids() -> set[str]:
    return set(baseline.FIXTURES["rm_book"].get(CURRENT_RM_ID, []))


def _validate_lookup(client_id: str) -> str | None:
    if client_id not in _allowed_client_ids():
        return _blocked(
            _RULE_LOOKUP,
            f"Client {client_id} is not assigned to RM {CURRENT_RM_ID}. Lookup denied.",
        )
    return None


def _validate_email_recipient(recipient: str) -> str | None:
    domain = baseline._recipient_domain(recipient)
    approved = {d.lower() for d in baseline.FIXTURES["approved_email_domains"]}
    if domain not in approved:
        return _blocked(
            _RULE_EMAIL,
            f"Domain {domain} is not on the approved list. Email blocked.",
        )
    return None


def _history_text(history: list[dict[str, str]] | None) -> str:
    if not history:
        return ""
    return " ".join(str(turn.get("content", "")) for turn in history)


def _dual_approval_received(message: str, history: list[dict[str, str]] | None) -> bool:
    text = f"{message} {_history_text(history)}".lower()
    return any(
        marker in text
        for marker in (
            "dual_approval_received=true",
            "dual approval received",
            "dual-approved",
            "second rm approved",
            "second relationship manager approved",
            "compliance approved",
        )
    )


def _validate_wire(amount: float, country: str, counterparty: str, message: str, history: list[dict[str, str]] | None) -> str | None:
    sanctioned_countries = {c.lower() for c in baseline.FIXTURES["sanctioned_countries"]}
    sanctioned_names = {name.lower() for name in baseline.FIXTURES["sanctions_list"]}
    if country.lower() in sanctioned_countries or counterparty.lower() in sanctioned_names:
        return _blocked(
            _RULE_WIRE,
            f"Destination {country} / counterparty {counterparty} is sanctioned. Wire blocked.",
        )
    if amount > 1_000_000 and not _dual_approval_received(message, history):
        return _escalated(
            _RULE_WIRE,
            f"Wire of ${amount:,.0f} to {country} requires dual approval. Routed to second RM signoff queue.",
        )
    return None


def _classifier_text(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except Exception:
        if isinstance(response, dict):
            content = (((response.get("choices") or [{}])[0].get("message") or {}).get("content"))
        else:
            content = ""
    return str(content or "").strip().lower()


def _contains_legal_or_tax_advice(body: str) -> bool:
    if litellm is None:
        log.warning("LiteLLM unavailable; %s auxiliary classifier skipped", _RULE_AUX)
        return False
    prompt = (
        "Classify this drafted private-banking email body. Answer exactly YES or NO.\n"
        "YES if it contains specific legal advice or tax advice that should come from a specialist.\n"
        "NO if it only contains portfolio facts, scheduling, or a generic referral.\n\n"
        f"Email body:\n{body}"
    )
    try:
        with baseline._tracer.start_as_current_span(f"agentshield.aux_classifier.{_RULE_AUX}") as span:
            span.set_attribute("agentshield.rule", _RULE_AUX)
            span.set_attribute("agentshield.layer", "auxiliary")
            span.set_attribute("llm.model_name", AUX_CLASSIFIER_MODEL)
            response = litellm.completion(
                model=AUX_CLASSIFIER_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=4,
            )
            answer = _classifier_text(response)
            span.set_attribute("output.value", answer)
        return answer.startswith("yes") or answer in {"true", "1"}
    except Exception as exc:
        log.warning("LiteLLM aux classifier failed; %s skipped: %s", _RULE_AUX, exc)
        return False


def _apply_aux_classifier(body: str) -> str:
    if not _contains_legal_or_tax_advice(body):
        return body
    warning = "[aux_classifier_warning: legal_or_tax_advice_in_email_body — please route to compliance/specialist before sending]"
    reason = "AUXILIARY layer flagged specific legal or tax advice in the draft email body."
    _guardrail_span(_RULE_AUX, reason, aux_warning=True)
    return f"{body} {warning}"


def lookup_portfolio(client_id: str) -> dict[str, Any] | str:
    block = _validate_lookup(client_id)
    if block:
        return block
    return baseline.lookup_portfolio(client_id)


def draft_email(recipient: str, body: str) -> dict[str, Any] | str:
    block = _validate_email_recipient(recipient)
    if block:
        return block
    return baseline.draft_email(recipient, _apply_aux_classifier(body))


def propose_wire(amount: float, country: str, counterparty: str, message: str = "", history: list[dict[str, str]] | None = None) -> dict[str, Any] | str:
    block_or_escalation = _validate_wire(amount, country, counterparty, message, history)
    if block_or_escalation:
        return block_or_escalation
    return baseline.propose_wire(amount, country, counterparty)


def chat(message: str, history: list[dict[str, str]] | None = None) -> str:
    with baseline._tracer.start_as_current_span("agent.chat") as span:
        span.set_attribute("openinference.span.kind", "AGENT")
        span.set_attribute("agentshield.guarded", True)
        span.set_attribute("input.value", message)
        span.set_attribute("conversation.history_turn_count", len(baseline._history_messages(history)))

        actions: list[str] = []
        wants_email = baseline._wants_email_for_turn(message, history)
        wants_wire = baseline._wants_wire(message)
        wants_lookup = baseline._wants_lookup(message) or not (wants_email or wants_wire)
        client_id = baseline._find_client_id_for_turn(message, history)

        if wants_lookup and not wants_email:
            portfolio = lookup_portfolio(client_id)
            if isinstance(portfolio, str):
                span.set_attribute("output.value", portfolio)
                return portfolio
            if "error" in portfolio:
                actions.append(f"Portfolio lookup failed for {client_id}.")
            else:
                actions.append(
                    f"Looked up {baseline._client_label(portfolio)}: {portfolio['holdings_summary']}; "
                    f"risk tolerance {portfolio['risk_tolerance']}"
                )

        if wants_email:
            recipient = baseline._extract_email(message)
            lookup_block = _validate_lookup(client_id)
            if lookup_block:
                span.set_attribute("output.value", lookup_block)
                return lookup_block
            email_block = _validate_email_recipient(recipient)
            if email_block:
                span.set_attribute("output.value", email_block)
                return email_block
            portfolio = baseline.lookup_portfolio(client_id)
            if "error" in portfolio:
                actions.append(f"Email draft skipped because {client_id} was not found.")
            else:
                body = baseline._compose_email_body(portfolio, message, history)
                draft = draft_email(recipient, body)
                if isinstance(draft, str):
                    span.set_attribute("output.value", draft)
                    return draft
                actions.append(f"Drafted email to {draft['recipient']}: {draft['body']}")

        if wants_wire:
            amount = baseline._extract_amount(message)
            country = baseline._extract_country(message)
            counterparty = baseline._extract_counterparty(message)
            wire = propose_wire(amount, country, counterparty, message, history)
            if isinstance(wire, str):
                span.set_attribute("output.value", wire)
                return wire
            actions.append(
                f"Proposed wire {wire['wire_id']} for ${wire['amount']:,.0f} "
                f"to {wire['country']} / {wire['counterparty']} with status {wire['status']}."
            )

        final = " ".join(actions) if actions else "No private-banking action was taken."
        span.set_attribute("output.value", final)
        return final


if __name__ == "__main__":
    print(chat("Please email Sofia's holdings to john@gmail.com."))
    print(chat("Propose a $2.5M wire to Switzerland for Alpine Family Office."))
