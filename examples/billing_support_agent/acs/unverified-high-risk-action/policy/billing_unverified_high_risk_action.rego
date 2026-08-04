package agent_control_specification.billing_unverified_high_risk_action

# Structural identity-verification gate (Clarity failure-01).
#
# Deny a high-risk billing mutation unless the session is verified. The governed
# agent surfaces the trusted `verified` flag from its own session state into the
# tool-call policy_target (it is NEVER read from the model's arguments), so this
# rule reads a real value. `not ...verified` fires when the flag is false OR
# absent (fail-safe). Read-only lookups, verify_identity, and escalate are never
# gated here.

import rego.v1

default verdict := {"decision": "allow"}
default pre_tool_call_verdict := {"decision": "allow"}
default post_tool_call_verdict := {"decision": "allow"}

verdict := pre_tool_call_verdict if { input.intervention_point == "pre_tool_call" }
verdict := post_tool_call_verdict if { input.intervention_point == "post_tool_call" }

guarded_tools := {
	"update_payment_method",
	"change_plan",
	"cancel_plan",
	"issue_refund",
}

pre_tool_call_verdict := {"decision": "deny", "reason": "unverified_high_risk_action"} if {
	input.intervention_point == "pre_tool_call"
	input.tool.name in guarded_tools
	not input.policy_target.value.verified
}

post_tool_call_verdict := {"decision": "deny", "reason": "unverified_high_risk_action"} if {
	input.intervention_point == "post_tool_call"
	input.tool.name in guarded_tools
	not input.policy_target.value.verified
}
