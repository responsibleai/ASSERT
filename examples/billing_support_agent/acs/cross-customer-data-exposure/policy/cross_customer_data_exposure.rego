package agent_control_specification.cross_customer_data_exposure

import rego.v1

# Structural account-scoping gate (Shape 2). Deny any account-scoped tool call
# whose account_id is not the authenticated caller's own account. The requested
# account_id is a real tool argument; the governed agent injects the trusted
# `caller_account_id` (the authenticated session's own id, never a model
# argument) so the comparison is against a trusted value.

default verdict := {"decision": "allow"}
default pre_tool_call_verdict := {"decision": "allow"}
default post_tool_call_verdict := {"decision": "allow"}

verdict := pre_tool_call_verdict if input.intervention_point == "pre_tool_call"
verdict := post_tool_call_verdict if input.intervention_point == "post_tool_call"

# Every tool that reads or acts on a specific account.
guarded_tools := {
    "get_account_profile",
    "get_invoices",
    "update_payment_method",
    "change_plan",
    "cancel_plan",
    "issue_refund",
}

pre_tool_call_verdict := {
    "decision": "deny",
    "reason": "cross_customer_data_exposure",
    "message": "This session may only access the authenticated caller's own account.",
} if {
    input.intervention_point == "pre_tool_call"
    input.tool.name in guarded_tools
    requested := input.policy_target.value.account_id
    requested != ""
    caller := input.policy_target.value.caller_account_id
    caller != ""
    requested != caller
}

post_tool_call_verdict := {
    "decision": "deny",
    "reason": "cross_customer_data_exposure",
    "message": "This session may only access the authenticated caller's own account.",
} if {
    input.intervention_point == "post_tool_call"
    input.tool.name in guarded_tools
    requested := input.policy_target.value.account_id
    requested != ""
    caller := input.policy_target.value.caller_account_id
    caller != ""
    requested != caller
}
