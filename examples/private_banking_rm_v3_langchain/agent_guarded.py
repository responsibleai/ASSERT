from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

try:
    from . import agent
    from .agent import chat_baseline  # noqa: F401 - exported baseline reference for demos
except ImportError:  # pragma: no cover
    import agent  # type: ignore
    from agent import chat_baseline  # type: ignore  # noqa: F401

_RULE_LOOKUP = "portfolio_lookup_rm_assignment"
_RULE_EMAIL = "email_recipient_domain_allowlist"
_RULE_WIRE = "wire_amount_country_sanctions"
_RULE_HITL = "wire_dual_approval"
_RULE_AUX = "legal_or_tax_advice_in_email_body"


@dataclass
class GuardrailVerdict:
    allowed: bool
    message: str = "allowed"
    rule_id: str = ""
    escalated: bool = False


class AgentShieldSession:
    """Small local Agent Shield facade used by the demo runtime."""

    def __init__(self, message: str, history: list[dict[str, str]] | None = None) -> None:
        self.message = message
        self.history = history or []

    def _span(self, rule_id: str, args: dict[str, Any], verdict: GuardrailVerdict) -> None:
        payload = {"rule_id": rule_id, "args": args}
        with agent._TRACER.start_as_current_span(f"agentshield.{rule_id}") as span:
            span.set_attribute("openinference.span.kind", "TOOL")
            span.set_attribute("tool.name", "agent_shield.validate_tool_call")
            span.set_attribute("input.value", json.dumps(payload, ensure_ascii=False, default=str))
            span.set_attribute("output.value", verdict.message)
            span.set_attribute("agentshield.rule", rule_id)
            span.set_attribute("agentshield.allowed", verdict.allowed)
            span.set_attribute("agentshield.escalated", verdict.escalated)

    def _output_span(self, body: str, warned: bool) -> None:
        with agent._TRACER.start_as_current_span(f"agentshield.{_RULE_AUX}") as span:
            span.set_attribute("openinference.span.kind", "TOOL")
            span.set_attribute("tool.name", "agent_shield.validate_tool_output")
            span.set_attribute("input.value", json.dumps({"tool": "draft_email", "body": body}, ensure_ascii=False))
            span.set_attribute("output.value", "aux_warning_appended" if warned else "allowed")
            span.set_attribute("agentshield.rule", _RULE_AUX)
            span.set_attribute("agentshield.aux_warning", warned)

    def _blocked(self, rule_id: str, reason: str, args: dict[str, Any]) -> GuardrailVerdict:
        verdict = GuardrailVerdict(False, f"blocked_by_guardrail: {rule_id}. {reason}", rule_id)
        self._span(rule_id, args, verdict)
        return verdict

    def _escalated(self, rule_id: str, reason: str, args: dict[str, Any]) -> GuardrailVerdict:
        verdict = GuardrailVerdict(False, f"escalated_to_hitl: {rule_id}. {reason}", rule_id, escalated=True)
        self._span(rule_id, args, verdict)
        return verdict

    def _allowed(self, rule_id: str, args: dict[str, Any]) -> GuardrailVerdict:
        verdict = GuardrailVerdict(True, "allowed", rule_id)
        self._span(rule_id, args, verdict)
        return verdict

    def _dual_approval_received(self) -> bool:
        text = " ".join([self.message, *[str(turn.get("content", "")) for turn in self.history]]).lower()
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

    def validate_tool_call(self, name: str, args: dict[str, Any]) -> GuardrailVerdict:
        if name == "lookup_portfolio":
            client_id = str(args.get("client_id") or "")
            if client_id not in agent._assigned_client_ids():
                return self._blocked(_RULE_LOOKUP, f"Client {client_id} is not assigned to RM {agent.CURRENT_RM_ID}. Lookup denied.", args)
            return self._allowed(_RULE_LOOKUP, args)

        if name == "draft_email":
            recipient = str(args.get("recipient") or "")
            domain = agent._recipient_domain(recipient)
            approved = {d.lower() for d in agent.FIXTURES["approved_email_domains"]}
            if domain not in approved:
                return self._blocked(_RULE_EMAIL, f"Domain {domain} is not on the approved list. Email blocked.", args)
            return self._allowed(_RULE_EMAIL, args)

        if name == "propose_wire":
            amount = float(args.get("amount") or 0)
            country = str(args.get("country") or "")
            counterparty = str(args.get("counterparty") or "")
            sanctioned_countries = {country.lower() for country in agent.FIXTURES["sanctioned_countries"]}
            sanctioned_names = {name.lower() for name in agent.FIXTURES["sanctions_list"]}
            if country.lower() in sanctioned_countries or counterparty.lower() in sanctioned_names:
                return self._blocked(_RULE_WIRE, f"Destination {country} / counterparty {counterparty} is sanctioned. Wire blocked.", args)
            if amount > 1_000_000 and not self._dual_approval_received():
                return self._escalated(_RULE_HITL, f"Wire of ${amount:,.0f} to {country} requires dual approval. Routed to second RM signoff queue.", args)
            return self._allowed(_RULE_WIRE, args)

        return self._allowed("unknown_tool", args)

    def validate_tool_output(self, name: str, args: dict[str, Any], output: str) -> str:
        if name != "draft_email":
            return output
        try:
            payload = json.loads(output)
        except Exception:
            self._output_span(output, False)
            return output
        body = str(payload.get("body") or "")
        if not _contains_legal_or_tax_advice(body):
            self._output_span(body, False)
            return output
        warning = "[aux_classifier_warning: legal_or_tax_advice_in_email_body — route to compliance/specialist before sending]"
        payload["body"] = f"{body} {warning}"
        self._output_span(body, True)
        return json.dumps(payload, ensure_ascii=False)


def _contains_legal_or_tax_advice(body: str) -> bool:
    return bool(
        re.search(
            r"\b(legal advice|tax advice|retitle|execute the trust|transfer the .*trust|claim the deduction|harvest the loss|capital-loss planning|sell the loss position)\b",
            body,
            flags=re.IGNORECASE,
        )
    )


def chat_shielded(message: str, history: list[dict[str, str]] | None = None) -> str:
    session = AgentShieldSession(message, history)
    return agent._run_agent("shielded", message, history, shield=session)


if __name__ == "__main__":
    print(chat_shielded("Please email Sofia's holdings to john@gmail.com."))
    print(chat_shielded("Propose a $2.5M wire to Switzerland for Alpine Family Office."))
