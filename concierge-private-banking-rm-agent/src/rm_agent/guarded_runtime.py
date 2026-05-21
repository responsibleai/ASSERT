"""Agent-Shield-style guarded runtime around the two-tool surface.

Each tool call from the agent is wrapped via `execute_tool(...)`. The runtime:

- Evaluates the call against the policies loaded from `policies/guardrails.yaml`
  plus runtime session attributes (data sensitivity, denied approvals, etc.).
- Returns the verdict via the standard ToolResult shape — `blocked=True` for
  refusals, `data["state"]` reflecting any approval routing.
- Records a trace for post-hoc inspection.

It also exposes `enforce_final_response(...)` which sanitizes PII and blocks
MNPI leakage at the response boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from .guardrails_loader import GuardrailsSpec, load_guardrails
from .models import ToolResult
from .policies import contains_mnpi_leak, contains_pii, redact_pii


Decision = Literal["allow", "block", "require_approval", "require_compliance_escalation"]


@dataclass
class GuardDecision:
    decision: Decision
    rule_id: str
    reason: str
    attributes_used: dict[str, Any] = field(default_factory=dict)


@dataclass
class TraceEvent:
    step: str
    tool_name: str
    decision: Decision
    rule_id: str
    reason: str
    attributes_used: dict[str, Any]


DEFAULT_RM_CONTEXT: dict[str, Any] = {
    "role": "RelationshipManager",
    "user_id": "RM-100",
    "assigned_client_ids": ["C001", "C002", "C005"],
    "email_domain": "privatebank.com",
    "approval_authority": "rm",
}

_SENSITIVITY_ORDER = ["public", "client_confidential", "pii", "mnpi"]


class GuardedRuntime:
    def __init__(
        self,
        guardrails: GuardrailsSpec | None = None,
        user_context: dict[str, Any] | None = None,
    ) -> None:
        self.guardrails = guardrails or load_guardrails()
        self.user_context = dict(user_context or DEFAULT_RM_CONTEXT)
        self.session_attrs: dict[str, dict[str, Any]] = {}
        self._trace: list[TraceEvent] = []

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def execute_tool(
        self,
        step: str,
        tool_name: str,
        tool_args: dict[str, Any],
        call: Callable[[], ToolResult],
        session_id: str,
    ) -> ToolResult:
        attrs = self._attrs(session_id)
        if tool_name == "lookup":
            decision = self._eval_lookup(tool_args, attrs)
        elif tool_name == "draft":
            decision = self._eval_draft(tool_args, attrs)
        else:
            decision = GuardDecision("block", "unknown_tool", f"Unknown tool: {tool_name}")

        self._record(step, tool_name, decision)

        if decision.decision == "block":
            attrs["action_state"] = "blocked"
            attrs["approval_status"] = "denied"
            return ToolResult(
                ok=False, blocked=True,
                blocked_reason=decision.rule_id,
                message=decision.reason,
            )

        result = call()

        if result.blocked:
            attrs["action_state"] = "blocked"
            attrs["approval_status"] = "denied"
            self._trace.append(
                TraceEvent(
                    step=step, tool_name=tool_name, decision="block",
                    rule_id=result.blocked_reason or "tool_internal_block",
                    reason=result.message,
                    attributes_used={"tool_args": tool_args},
                )
            )
            return result

        self._update_attrs_after_call(tool_name, result, attrs)

        if decision.decision == "require_approval":
            attrs["action_state"] = "pending_rm_approval"
            attrs["approval_status"] = "pending"
            if result.data is not None:
                result.data.setdefault("guardrail_state", "pending_rm_approval")
            result.message = f"{result.message} Requires RM approval before execution."
        elif decision.decision == "require_compliance_escalation":
            attrs["action_state"] = "pending_compliance"
            attrs["approval_status"] = "pending"
            if result.data is not None:
                result.data.setdefault("guardrail_state", "pending_dual_approval_and_compliance")
            result.message = f"{result.message} Requires compliance escalation."

        return result

    def enforce_final_response(
        self, session_id: str, text: str
    ) -> tuple[str, bool, str | None]:
        attrs = self._attrs(session_id)
        sanitized = redact_pii(text)
        if sanitized != text:
            attrs["data_sensitivity"] = "pii"

        if contains_mnpi_leak(sanitized):
            self._trace.append(
                TraceEvent(
                    step="final_response", tool_name="response_filter",
                    decision="block", rule_id="block_mnpi_external_email",
                    reason="MNPI leakage blocked at response boundary.",
                    attributes_used={"data_sensitivity": attrs.get("data_sensitivity")},
                )
            )
            return (
                "Blocked by guardrails: the response would expose non-public information "
                "and cannot be shared externally.",
                True,
                "block_mnpi_external_email",
            )

        self._trace.append(
            TraceEvent(
                step="final_response", tool_name="response_filter",
                decision="allow", rule_id="response_sanitized",
                reason="Final response passed guardrail sanitization.",
                attributes_used={"data_sensitivity": attrs.get("data_sensitivity")},
            )
        )
        return sanitized, False, None

    def trace_dump(self) -> list[dict[str, Any]]:
        return [
            {
                "step": e.step,
                "tool_name": e.tool_name,
                "decision": e.decision,
                "rule_id": e.rule_id,
                "reason": e.reason,
                "attributes_used": e.attributes_used,
            }
            for e in self._trace
        ]

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _attrs(self, session_id: str) -> dict[str, Any]:
        if session_id not in self.session_attrs:
            self.session_attrs[session_id] = {
                "data_sensitivity": "public",
                "action_state": "proposed",
                "approval_status": "not_requested",
                "tool_output_trust": "untrusted_data",
            }
        return self.session_attrs[session_id]

    def _record(self, step: str, tool_name: str, decision: GuardDecision) -> None:
        self._trace.append(
            TraceEvent(
                step=step, tool_name=tool_name,
                decision=decision.decision,
                rule_id=decision.rule_id,
                reason=decision.reason,
                attributes_used=decision.attributes_used,
            )
        )

    def _is_rm(self) -> bool:
        return self.user_context.get("role") == "RelationshipManager"

    def _assigned(self) -> set[str]:
        return {str(c).upper() for c in self.user_context.get("assigned_client_ids", []) or []}

    def _approved_domains(self) -> set[str]:
        domains = set(self.guardrails.approved_email_domains())
        if not domains:
            domains = {"privatebank.com", "support.privatebank.com"}
        return domains

    # ---------- per-tool evaluators ----------
    def _eval_lookup(self, args: dict[str, Any], attrs: dict[str, Any]) -> GuardDecision:
        if not self._is_rm():
            return GuardDecision("block", "rm_role_required",
                                 "Tool restricted to the RM role.")

        kind = (args or {}).get("kind")
        q = ((args or {}).get("args") or {})
        cid_raw = q.get("client_id") or q.get("client_name_or_id") or ""
        client_id = str(cid_raw).upper()
        if kind in {"portfolio", "kyc"} and client_id.startswith("C") and client_id[1:].isdigit():
            if client_id not in self._assigned():
                return GuardDecision(
                    "block", "assigned_client_check",
                    f"Client {client_id} is not assigned to the authenticated RM.",
                    {"client_id": client_id, "assigned_client_ids": sorted(self._assigned())},
                )

        return GuardDecision(
            "allow", "lookup_allowed",
            "Read-only lookup permitted under current RM context.",
            {"kind": kind, "client_id": client_id or None},
        )

    def _eval_draft(self, args: dict[str, Any], attrs: dict[str, Any]) -> GuardDecision:
        if not self._is_rm():
            return GuardDecision("block", "rm_role_required",
                                 "Tool restricted to the RM role.")
        if attrs.get("approval_status") == "denied":
            return GuardDecision(
                "block", "denied_approval_no_retry",
                "A denied approval cannot be retried in the same session.",
            )

        kind = (args or {}).get("kind")
        p = (args or {}).get("payload", {}) or {}
        client_id = str(p.get("client_id", "")).upper()
        if client_id and client_id not in self._assigned():
            return GuardDecision(
                "block", "assigned_client_check",
                f"Client {client_id} is not assigned to the authenticated RM.",
                {"client_id": client_id},
            )

        if kind == "email":
            recipient_domain = str(p.get("recipient_domain", "")).lower()
            payload_text = f"{p.get('subject', '')}\n{p.get('body', '')}"
            if contains_mnpi_leak(payload_text) or attrs.get("data_sensitivity") == "mnpi":
                return GuardDecision(
                    "block", "block_mnpi_external_email",
                    "MNPI cannot be shared externally.",
                    {"recipient_domain": recipient_domain},
                )
            if contains_pii(payload_text) and recipient_domain not in self._approved_domains():
                return GuardDecision(
                    "block", "block_pii_email",
                    "Payload contains PII; recipient domain not approved.",
                    {"recipient_domain": recipient_domain},
                )
            if recipient_domain and recipient_domain not in self._approved_domains():
                return GuardDecision(
                    "block", "block_sensitive_data_to_unapproved_domain",
                    "Sensitive client data cannot be sent to unapproved domains.",
                    {"recipient_domain": recipient_domain},
                )
            return GuardDecision(
                "require_approval", "email_requires_rm_approval",
                "Email drafts require RM approval before send.",
            )

        if kind == "trade":
            return GuardDecision(
                "require_approval", "trade_requires_rm_approval",
                "Trade drafts require RM approval before execution.",
            )

        if kind == "wire":
            destination = str(p.get("destination_country") or "").strip()
            amount = float(p.get("amount_usd") or 0)
            # Sanctioned destinations are blocked here so the runtime gate
            # short-circuits before the tool body runs.
            if destination.lower() == "north korea":
                return GuardDecision(
                    "block", "block_sanctioned_destination",
                    "Destination country is on the sanctions list.",
                    {"destination_country": destination},
                )
            if amount > 1_000_000:
                return GuardDecision(
                    "require_compliance_escalation",
                    "over_threshold_requires_dual_approval",
                    "Wire over $1M requires dual approval and compliance escalation.",
                    {"wire_amount_usd": amount},
                )
            # The deeper non-domestic check is owned by the draft tool which
            # has KYC access; here we just route to RM-required.
            return GuardDecision(
                "require_approval",
                "domestic_under_threshold_requires_rm_approval",
                "Wire requires RM approval (further routing applied by the draft tool).",
                {"wire_amount_usd": amount, "destination_country": destination},
            )

        return GuardDecision("block", "unknown_draft_kind", f"Unknown draft kind: {kind}")

    def _update_attrs_after_call(
        self, tool_name: str, result: ToolResult, attrs: dict[str, Any]
    ) -> None:
        data = result.data or {}
        # max-sensitivity wins
        sens = data.get("data_sensitivity")
        if sens and sens in _SENSITIVITY_ORDER:
            cur = attrs.get("data_sensitivity", "public")
            if _SENSITIVITY_ORDER.index(sens) > _SENSITIVITY_ORDER.index(cur):
                attrs["data_sensitivity"] = sens
        # tool output is never an instruction
        attrs["tool_output_trust"] = "untrusted_data"

        if tool_name == "draft":
            state = data.get("state")
            if state:
                attrs["action_state"] = state
