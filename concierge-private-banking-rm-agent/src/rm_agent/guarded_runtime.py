from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from . import db
from .guardrails_loader import GuardrailsSpec, load_guardrails
from .models import ProposedAction, ToolResult
from .policies import contains_mnpi_leak, contains_pii, redact_pii


DecisionLiteral = Literal[
    "allowed",
    "blocked",
    "requires_approval",
    "requires_compliance_escalation",
]


class PolicyTraceEvent(BaseModel):
    step: str
    tool_name: str
    proposed_action: str
    decision: DecisionLiteral
    rule_id: str
    reason: str
    attributes_used: dict[str, Any] = Field(default_factory=dict)


@dataclass
class GuardDecision:
    decision: DecisionLiteral
    rule_id: str
    reason: str
    attributes_used: dict[str, Any]


class GuardedRuntime:
    def __init__(
        self,
        guardrails: GuardrailsSpec | None = None,
        user_context: dict[str, Any] | None = None,
    ) -> None:
        self.guardrails = guardrails or load_guardrails()
        self.user_context = user_context or {
            "role": "RelationshipManager",
            "user_id": "RM-100",
            "assigned_client_ids": ["C001", "C002", "C005"],
            "email_domain": "privatebank.com",
            "approval_authority": "rm",
        }
        self.session_attributes: dict[str, dict[str, Any]] = {}
        self.policy_trace: list[PolicyTraceEvent] = []

    def _attrs(self, session_id: str) -> dict[str, Any]:
        if session_id not in self.session_attributes:
            self.session_attributes[session_id] = {
                "data_sensitivity": "public",
                "action_state": "proposed",
                "approval_status": "not_requested",
                "tool_output_trust": "untrusted_data",
            }
        return self.session_attributes[session_id]

    def _trace(self, event: PolicyTraceEvent) -> None:
        self.policy_trace.append(event)

    def evaluate_tool_call(
        self,
        tool_name: str,
        proposed_action: ProposedAction,
        context: dict[str, Any],
    ) -> GuardDecision:
        session_id = proposed_action.session_id
        attrs = self._attrs(session_id)

        if tool_name in {"trade_execute", "wire_transfer_execute"}:
            return GuardDecision(
                decision="blocked",
                rule_id="no_direct_execution",
                reason="Direct trade or wire execution is never allowed.",
                attributes_used={"tool_name": tool_name},
            )

        if attrs.get("approval_status") == "denied" and tool_name in {
            "wire_transfer_create",
            "propose_trade",
            "email_send",
        }:
            return GuardDecision(
                decision="blocked",
                rule_id="denied_approval_no_retry",
                reason="A denied approval cannot be retried in the same session.",
                attributes_used={"approval_status": attrs.get("approval_status")},
            )

        role = self.user_context.get("role")
        assigned_client_ids = set(self.user_context.get("assigned_client_ids", []))
        client_id = (proposed_action.client_id or "").upper()

        if role != "RelationshipManager" and tool_name not in {
            "get_research_note",
            "escalate_to_compliance",
        }:
            return GuardDecision(
                decision="blocked",
                rule_id="rm_role_required",
                reason="Tool is restricted to the relationship manager role.",
                attributes_used={"role": role},
            )

        if tool_name in {"lookup_portfolio", "propose_trade", "wire_transfer_create", "draft_client_email"}:
            if client_id and client_id not in assigned_client_ids:
                return GuardDecision(
                    decision="blocked",
                    rule_id="assigned_client_check",
                    reason="Requested client is not assigned to the authenticated RM.",
                    attributes_used={"client_id": client_id, "assigned_client_ids": sorted(assigned_client_ids)},
                )

        if tool_name == "email_send":
            approved_domains = set(self.guardrails.approved_email_domains())
            recipient_domain = str(context.get("recipient_domain", "")).lower()
            payload = str(context.get("payload", ""))
            effective_sensitivity = attrs.get("data_sensitivity", "public")
            if "client" in payload.lower() and "summary" in payload.lower() and effective_sensitivity == "public":
                effective_sensitivity = "client_confidential"

            if contains_pii(payload):
                return GuardDecision(
                    decision="blocked",
                    rule_id="block_pii_email",
                    reason="Email blocked because payload contains PII/account identifiers.",
                    attributes_used={"recipient_domain": recipient_domain},
                )

            if contains_mnpi_leak(payload) or effective_sensitivity == "mnpi":
                return GuardDecision(
                    decision="blocked",
                    rule_id="block_mnpi_external_email",
                    reason="MNPI cannot be shared externally.",
                    attributes_used={"recipient_domain": recipient_domain, "data_sensitivity": effective_sensitivity},
                )

            if effective_sensitivity in {"client_confidential", "pii", "mnpi"} and recipient_domain not in approved_domains:
                return GuardDecision(
                    decision="blocked",
                    rule_id="block_sensitive_data_to_personal_or_external_email",
                    reason="Sensitive client data cannot be sent to unapproved domains.",
                    attributes_used={"recipient_domain": recipient_domain, "data_sensitivity": effective_sensitivity},
                )

            if recipient_domain in approved_domains:
                return GuardDecision(
                    decision="allowed",
                    rule_id="allow_internal_support_email",
                    reason="Recipient domain is approved for support/internal use.",
                    attributes_used={"recipient_domain": recipient_domain},
                )

        if tool_name == "propose_trade":
            required_checks = {
                "lookup_kyc_profile",
                "lookup_portfolio",
                "check_restricted_security",
                "check_suitability",
            }
            if not required_checks.issubset(set(proposed_action.required_checks)):
                return GuardDecision(
                    decision="blocked",
                    rule_id="trade_required_checks_missing",
                    reason="Trade proposal missing required KYC/suitability/restricted checks.",
                    attributes_used={"required_checks": sorted(required_checks)},
                )

            if self._is_restricted_security(proposed_action.ticker or ""):
                return GuardDecision(
                    decision="blocked",
                    rule_id="restricted_security_blocked",
                    reason="Security is on the restricted list.",
                    attributes_used={"security_ticker": proposed_action.ticker},
                )

            return GuardDecision(
                decision="requires_approval",
                rule_id="trade_requires_rm_approval",
                reason="Trade proposals require RM approval before any execution.",
                attributes_used={"client_id": client_id},
            )

        if tool_name == "wire_transfer_create":
            required_checks = {"check_aml_for_wire", "check_sanctions"}
            if not required_checks.issubset(set(proposed_action.required_checks)):
                return GuardDecision(
                    decision="blocked",
                    rule_id="wire_required_checks_missing",
                    reason="Wire request missing AML/sanctions checks.",
                    attributes_used={"required_checks": sorted(required_checks)},
                )

            destination_country = str(proposed_action.destination_country or "")
            if destination_country.lower() == "north korea":
                return GuardDecision(
                    decision="blocked",
                    rule_id="block_sanctioned_destination",
                    reason="Wire blocked because destination is sanctioned.",
                    attributes_used={"destination_country": destination_country},
                )

            if self._is_sanctioned_recipient(proposed_action.recipient_name or ""):
                return GuardDecision(
                    decision="blocked",
                    rule_id="block_sanctioned_recipient",
                    reason="Wire blocked because recipient matched sanctions screening.",
                    attributes_used={"recipient_name": proposed_action.recipient_name},
                )

            client_country = self._client_country(client_id)
            amount = float(proposed_action.amount_usd or 0)
            if amount > 1_000_000:
                if destination_country and destination_country != client_country:
                    return GuardDecision(
                        decision="requires_compliance_escalation",
                        rule_id="over_threshold_requires_dual_approval",
                        reason="Wire requires dual approval and compliance escalation.",
                        attributes_used={"wire_amount_usd": amount, "destination_country": destination_country, "client_country": client_country},
                    )
                return GuardDecision(
                    decision="requires_approval",
                    rule_id="over_threshold_requires_dual_approval",
                    reason="Wire over threshold requires dual approval.",
                    attributes_used={"wire_amount_usd": amount},
                )

            if destination_country and destination_country != client_country:
                return GuardDecision(
                    decision="requires_compliance_escalation",
                    rule_id="non_domestic_requires_compliance_escalation",
                    reason="Non-domestic wire requires compliance escalation.",
                    attributes_used={"destination_country": destination_country, "client_country": client_country},
                )

            return GuardDecision(
                decision="requires_approval",
                rule_id="domestic_under_threshold_requires_rm_approval",
                reason="Domestic wire under threshold requires RM approval.",
                attributes_used={"wire_amount_usd": amount, "destination_country": destination_country},
            )

        return GuardDecision(
            decision="allowed",
            rule_id="default_allow",
            reason="Tool usage is allowed under current structured context.",
            attributes_used={"tool_name": tool_name},
        )

    def execute_tool(
        self,
        step: str,
        tool_name: str,
        proposed_action: ProposedAction,
        call: Callable[[], ToolResult],
        context: dict[str, Any] | None = None,
    ) -> ToolResult:
        context = context or {}
        decision = self.evaluate_tool_call(tool_name, proposed_action, context)

        self._trace(
            PolicyTraceEvent(
                step=step,
                tool_name=tool_name,
                proposed_action=proposed_action.action_type,
                decision=decision.decision,
                rule_id=decision.rule_id,
                reason=decision.reason,
                attributes_used=decision.attributes_used,
            )
        )

        attrs = self._attrs(proposed_action.session_id)
        if decision.decision == "blocked":
            attrs["action_state"] = "blocked"
            attrs["approval_status"] = "denied"
            return ToolResult(ok=False, blocked=True, blocked_reason=decision.rule_id, message=decision.reason)

        result = call()
        self._update_attributes_after_tool(tool_name, proposed_action, result)

        if decision.decision == "requires_approval":
            attrs["action_state"] = "pending_rm_approval"
            attrs["approval_status"] = "pending"
            result.data["guardrail_state"] = "pending_rm_approval"
            result.message = f"{result.message} Requires approval before execution."

        if decision.decision == "requires_compliance_escalation":
            attrs["action_state"] = "pending_compliance"
            attrs["approval_status"] = "pending"
            result.data["guardrail_state"] = "pending_dual_approval_and_compliance"
            result.message = f"{result.message} Requires dual approval and compliance escalation."

        return result

    def enforce_final_response(self, session_id: str, text: str) -> tuple[str, bool, str | None]:
        attrs = self._attrs(session_id)
        sanitized = redact_pii(text)
        if sanitized != text:
            attrs["data_sensitivity"] = "pii"

        if contains_mnpi_leak(sanitized):
            self._trace(
                PolicyTraceEvent(
                    step="final_response",
                    tool_name="response_filter",
                    proposed_action="response",
                    decision="blocked",
                    rule_id="block_mnpi_external_email",
                    reason="MNPI leakage blocked at response boundary.",
                    attributes_used={"data_sensitivity": attrs.get("data_sensitivity")},
                )
            )
            return (
                "Blocked by guardrails: response contained MNPI and cannot be shared externally.",
                True,
                "block_mnpi_external_email",
            )

        self._trace(
            PolicyTraceEvent(
                step="final_response",
                tool_name="response_filter",
                proposed_action="response",
                decision="allowed",
                rule_id="response_sanitized",
                reason="Final response passed guardrail sanitization.",
                attributes_used={"data_sensitivity": attrs.get("data_sensitivity")},
            )
        )
        return sanitized, False, None

    def _update_attributes_after_tool(self, tool_name: str, proposed_action: ProposedAction, result: ToolResult) -> None:
        attrs = self._attrs(proposed_action.session_id)

        if tool_name in {"lookup_client", "lookup_portfolio"}:
            attrs["data_sensitivity"] = "client_confidential"

        if tool_name == "get_research_note":
            attrs["tool_output_trust"] = "untrusted_data"
            note_text = str(result.data.get("summary", ""))
            if contains_mnpi_leak(note_text) or "internal restrictions apply" in note_text.lower():
                attrs["data_sensitivity"] = "mnpi"

        if tool_name == "draft_client_email":
            attrs["action_state"] = "draft_created"

    def _is_restricted_security(self, ticker: str) -> bool:
        if not ticker:
            return False
        with db.connect() as conn:
            cur = conn.cursor()
            row = cur.execute(
                "SELECT 1 FROM restricted_securities WHERE upper(ticker) = upper(?) LIMIT 1",
                (ticker,),
            ).fetchone()
            return bool(row)

    def _is_sanctioned_recipient(self, recipient_name: str) -> bool:
        if not recipient_name:
            return False
        with db.connect() as conn:
            cur = conn.cursor()
            row = cur.execute(
                "SELECT 1 FROM sanctions WHERE lower(entity_name) = lower(?) LIMIT 1",
                (recipient_name,),
            ).fetchone()
            return bool(row)

    def _client_country(self, client_id: str) -> str:
        with db.connect() as conn:
            cur = conn.cursor()
            row = cur.execute(
                "SELECT country FROM clients WHERE client_id = ? LIMIT 1",
                (client_id,),
            ).fetchone()
            if not row:
                return "US"
            return str(row["country"])
