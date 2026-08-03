package agent_control_specification.unverified_high_risk_action

import rego.v1

# Structural verification gate (Shape 1). Deny a high-risk billing mutation
# unless the session is verified. The governed agent injects the trusted
# `verified` session flag into the tool-call policy_target (it is NOT a model
# argument), so `not ...verified` fires on both false and missing.

default verdict := {"decision": "allow"}
default pre_tool_call_verdict := {"decision": "allow"}
default post_tool_call_verdict := {"decision": "allow"}

verdict := pre_tool_call_verdict if input.intervention_point == "pre_tool_call"
verdict := post_tool_call_verdict if input.intervention_point == "post_tool_call"

# High-risk tools that require a verified session.
guarded_tools := {"update_payment_method", "change_plan", "cancel_plan", "issue_refund"}

pre_tool_call_verdict := {
    "decision": "deny",
    "reason": "unverified_high_risk_action",
    "message": "Identity verification is required before a payment, plan, cancellation, or refund action.",
} if {
    input.intervention_point == "pre_tool_call"
    input.tool.name in guarded_tools
    not input.policy_target.value.verified
}

post_tool_call_verdict := {
    "decision": "deny",
    "reason": "unverified_high_risk_action",
    "message": "Identity verification is required before a payment, plan, cancellation, or refund action.",
} if {
    input.intervention_point == "post_tool_call"
    input.tool.name in guarded_tools
    not input.policy_target.value.verified
}
