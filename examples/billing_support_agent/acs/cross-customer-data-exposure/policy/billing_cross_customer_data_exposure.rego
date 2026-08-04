package agent_control_specification.billing_cross_customer_data_exposure

# Structural account-scoping gate (Clarity failure-02, OWASP API1:2023 BOLA).
#
# Deny any read or write whose target account differs from the authenticated
# caller. `account_id` comes from the tool arguments (or result); the trusted
# `caller_account_id` is injected by the governed agent from its own session
# state, never from the model. An empty requested account is left to the agent's
# own-account default, so legitimate own-account calls pass untouched.
# verify_identity and escalate_to_human are never gated here.

import rego.v1

default verdict := {"decision": "allow"}
default pre_tool_call_verdict := {"decision": "allow"}
default post_tool_call_verdict := {"decision": "allow"}

verdict := pre_tool_call_verdict if { input.intervention_point == "pre_tool_call" }
verdict := post_tool_call_verdict if { input.intervention_point == "post_tool_call" }

guarded_tools := {
	"get_account_profile",
	"get_invoices",
	"update_payment_method",
	"change_plan",
	"cancel_plan",
	"issue_refund",
}

pre_tool_call_verdict := {"decision": "deny", "reason": "cross_customer_data_exposure"} if {
	input.intervention_point == "pre_tool_call"
	input.tool.name in guarded_tools
	requested := input.policy_target.value.account_id
	requested != ""
	requested != input.policy_target.value.caller_account_id
}

post_tool_call_verdict := {"decision": "deny", "reason": "cross_customer_data_exposure"} if {
	input.intervention_point == "post_tool_call"
	input.tool.name in guarded_tools
	requested := input.policy_target.value.account_id
	requested != ""
	requested != input.policy_target.value.caller_account_id
}
