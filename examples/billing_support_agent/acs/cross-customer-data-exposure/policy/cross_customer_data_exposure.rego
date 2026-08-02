# ACS policy — Clarity failure-02: cross-customer data exposure (BOLA).
#
# Structural argument gate (Shape 2). Every account-scoped tool must target the
# caller's OWN account. The requested account_id is a real tool arg; the trusted
# comparison value (caller_account_id) is injected by the governed agent from its
# own session state — never trusted from a second user-supplied arg. Deny when
# the requested account differs from the caller's.
#
# OPA input contract:
#   input.intervention_point   "pre_tool_call" | "post_tool_call"
#   input.tool.name            the tool being called
#   input.policy_target.value  the tool args + injected .caller_account_id

package agent_control_specification.cross_customer_data_exposure

import rego.v1

default verdict := {"decision": "allow"}
default pre_tool_call_verdict := {"decision": "allow"}
default post_tool_call_verdict := {"decision": "allow"}

verdict := pre_tool_call_verdict if input.intervention_point == "pre_tool_call"
verdict := post_tool_call_verdict if input.intervention_point == "post_tool_call"

# Tools that take an account_id and could reach another customer's data.
account_tools := {
	"get_account_profile",
	"get_invoices",
	"update_payment_method",
	"change_plan",
	"cancel_plan",
	"issue_refund",
}

tool_name := object.get(object.get(input, "tool", {}), "name", "")
target_value := object.get(input.policy_target, "value", {})

requested_account := object.get(target_value, "account_id", "")
caller_account := object.get(target_value, "caller_account_id", "")

# Deny an account-scoped call whose requested account differs from the caller's
# own (trusted, injected) account id.
pre_tool_call_verdict := {
	"decision": "deny",
	"reason": "cross_customer_access",
	"message": "I can only access your own account. Only this cross-account request is blocked - I can still help with your own invoices, profile, and account actions, or escalate to a human.",
} if {
	input.intervention_point == "pre_tool_call"
	tool_name in account_tools
	requested_account != ""
	caller_account != ""
	requested_account != caller_account
}
