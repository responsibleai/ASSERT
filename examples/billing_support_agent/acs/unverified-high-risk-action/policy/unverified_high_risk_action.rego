# ACS policy — Clarity failure-01: unverified high-risk action.
#
# Structural session-state gate (Shape 1). The four high-risk tools may only run
# on a VERIFIED session. Verification is a trusted flag the governed agent
# injects into the tool-call policy_target from its own session state (set by a
# successful verify_identity call) — never from the model's arguments. `not
# args.verified` denies when the flag is false OR absent (fail-closed).
#
# OPA input contract (populated by the governed agent / ACS host):
#   input.intervention_point   "pre_tool_call" | "post_tool_call"
#   input.tool.name            the tool being called
#   input.policy_target.value  at pre_tool_call: the tool args + injected trusted
#                              context (.verified, .caller_account_id);
#                              at post_tool_call: the tool result (.verified_session)

package agent_control_specification.unverified_high_risk_action

import rego.v1

default verdict := {"decision": "allow"}
default pre_tool_call_verdict := {"decision": "allow"}
default post_tool_call_verdict := {"decision": "allow"}

verdict := pre_tool_call_verdict if input.intervention_point == "pre_tool_call"
verdict := post_tool_call_verdict if input.intervention_point == "post_tool_call"

# The high-risk tools that require a verified session.
guarded_tools := {"update_payment_method", "change_plan", "cancel_plan", "issue_refund"}

tool_name := object.get(object.get(input, "tool", {}), "name", "")
target_value := object.get(input.policy_target, "value", {})

# pre_tool_call: deny a high-risk call on an unverified session. `not verified`
# fires on both false and missing, so it fails closed.
pre_tool_call_verdict := {
	"decision": "deny",
	"reason": "unverified_high_risk_action",
	"message": "Identity verification is required before this action. Only THIS high-risk action is blocked - read-only help (invoices, profile) and escalation are still available, and I can verify you now and then retry.",
} if {
	input.intervention_point == "pre_tool_call"
	tool_name in guarded_tools
	not target_value.verified
}

# post_tool_call: defense in depth on the result, which echoes verified_session.
post_tool_call_verdict := {
	"decision": "deny",
	"reason": "unverified_high_risk_action",
	"message": "Identity verification is required before this action.",
} if {
	input.intervention_point == "post_tool_call"
	tool_name in guarded_tools
	not object.get(target_value, "verified_session", false)
}
