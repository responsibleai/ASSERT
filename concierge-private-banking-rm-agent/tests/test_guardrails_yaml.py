from __future__ import annotations

from rm_agent.guardrails_loader import REQUIRED_TOP_LEVEL_KEYS, load_guardrails
from rm_agent.guarded_runtime import GuardedRuntime
from rm_agent.models import ProposedAction, ToolResult


def test_guardrails_yaml_has_required_sections():
    spec = load_guardrails()
    missing = sorted(REQUIRED_TOP_LEVEL_KEYS - set(spec.raw.keys()))
    assert missing == []


def test_guarded_runtime_blocks_unassigned_portfolio_lookup():
    runtime = GuardedRuntime()
    action = ProposedAction(action_type="lookup", client_id="C003", session_id="s-portfolio")

    out = runtime.execute_tool(
        step="prepare_action",
        tool_name="lookup_portfolio",
        proposed_action=action,
        call=lambda: ToolResult(message="should not execute"),
    )

    assert out.blocked is True
    assert out.blocked_reason == "assigned_client_check"


def test_guarded_runtime_blocks_sensitive_gmail_email():
    runtime = GuardedRuntime()
    action = ProposedAction(action_type="email", client_id="C001", session_id="s-email")
    runtime.session_attributes[action.session_id] = {
        "data_sensitivity": "client_confidential",
        "action_state": "draft_created",
        "approval_status": "not_requested",
        "tool_output_trust": "untrusted_data",
    }

    out = runtime.execute_tool(
        step="prepare_action",
        tool_name="email_send",
        proposed_action=action,
        context={"recipient_domain": "gmail.com", "payload": "client summary"},
        call=lambda: ToolResult(message="should not execute"),
    )

    assert out.blocked is True
    assert out.blocked_reason == "block_sensitive_data_to_personal_or_external_email"


def test_guarded_runtime_escalates_large_non_domestic_wire():
    runtime = GuardedRuntime()
    action = ProposedAction(
        action_type="wire",
        client_id="C001",
        session_id="s-wire",
        amount_usd=2_000_000,
        destination_country="Switzerland",
        recipient_name="Trusted Recipient",
        required_checks=["check_aml_for_wire", "check_sanctions"],
    )

    out = runtime.execute_tool(
        step="prepare_action",
        tool_name="wire_transfer_create",
        proposed_action=action,
        call=lambda: ToolResult(message="wire drafted", data={}),
    )

    assert out.blocked is False
    assert out.data.get("guardrail_state") == "pending_dual_approval_and_compliance"
