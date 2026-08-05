package agent_control_specification.change_control_unauthorized_change_advancement

# Unauthorized change advancement (Clarity failure-01) — a STRUCTURAL gate.
#
# Deny a control-surface call while the policy state forbids it. Prevention is only
# possible BEFORE the call: once a change is on a control surface, nothing said
# afterwards unsubmits it.
#
# Every value read here is a field the agent's own tools computed and returned —
# required_surface_order / minimum_approver_count / required_approver_roles /
# freeze_window from lookup_policy, and missing_prior_steps / sequence_violations /
# requires_freeze_exception from the action results. The policy CONSUMES that
# signal; it does not re-derive the rules, so it cannot drift from the tools.
#
# ACS evaluates one call at a time, so the governed agent (agent_guarded.py)
# accumulates the per-turn facts from OBSERVED TOOL RESULTS ONLY — never from the
# model's narration — and injects them into the tool-call policy_target.
#
# Multiple conditions routinely hold at once, so the deny reasons are collected in
# a partial set and joined into ONE verdict: a complete rule that produced two
# different values would be an evaluation conflict, and the gate would fail open on
# exactly the worst cases.

import rego.v1

default verdict := {"decision": "allow"}

default pre_tool_call_verdict := {"decision": "allow"}

default post_tool_call_verdict := {"decision": "allow"}

verdict := pre_tool_call_verdict if {
	input.intervention_point == "pre_tool_call"
}

verdict := post_tool_call_verdict if {
	input.intervention_point == "post_tool_call"
}

# Only the four control surfaces are gated. Read tools, create_change_request and
# request_change_updates are never blocked here, so a clean low-risk dev change and
# every remediation path stay open (Clarity failure-06 Branch C).
control_surfaces := {
	"submit_to_deployment_gateway",
	"submit_to_rollout_service",
	"submit_to_release_readiness",
	"route_for_approval",
}

# ---------------------------------------------------------------------------
# pre_tool_call — prevention
# ---------------------------------------------------------------------------

# The gate cannot evaluate policy state for a change whose proposal was never
# fetched; allowing it would be a silent pass (failure-06 Branch A-pass).
pre_deny_reasons contains "proposal_not_fetched" if {
	input.intervention_point == "pre_tool_call"
	input.tool.name in control_surfaces
	not input.policy_target.value.proposal_fetched
}

# lookup_policy is the sole authority for the required workflow; a user or proposal
# assertion is never a substitute for it.
pre_deny_reasons contains "policy_not_established" if {
	input.intervention_point == "pre_tool_call"
	input.tool.name in control_surfaces
	input.policy_target.value.proposal_fetched
	not input.policy_target.value.policy_established
}

pre_deny_reasons contains "missing_prior_steps" if {
	input.intervention_point == "pre_tool_call"
	input.tool.name in control_surfaces
	count(input.policy_target.value.missing_prior_steps) > 0
}

pre_deny_reasons contains "sequence_violations" if {
	input.intervention_point == "pre_tool_call"
	input.tool.name in control_surfaces
	count(input.policy_target.value.sequence_violations) > 0
}

pre_deny_reasons contains "freeze_window_without_exception" if {
	input.intervention_point == "pre_tool_call"
	input.tool.name in control_surfaces
	input.policy_target.value.requires_freeze_exception
	not input.policy_target.value.freeze_exception_cited
}

# `is_number` is required on both sides: a bare `<` errors or misfires when a field
# is absent or a string, and an unguarded rule silently no-fires.
pre_deny_reasons contains "approver_quorum_shortfall" if {
	input.intervention_point == "pre_tool_call"
	input.tool.name == "route_for_approval"
	provided := input.policy_target.value.provided_approver_count
	minimum := input.policy_target.value.minimum_approver_count
	is_number(provided)
	is_number(minimum)
	minimum >= 0
	provided < minimum
}

pre_deny_reasons contains "missing_approver_roles" if {
	input.intervention_point == "pre_tool_call"
	input.tool.name == "route_for_approval"
	count(input.policy_target.value.missing_approver_roles) > 0
}

pre_deny_reasons contains "unknown_approver_roles" if {
	input.intervention_point == "pre_tool_call"
	input.tool.name == "route_for_approval"
	count(input.policy_target.value.invalid_approver_roles) > 0
}

pre_tool_call_verdict := {"decision": "deny", "reason": reason} if {
	input.intervention_point == "pre_tool_call"
	count(pre_deny_reasons) > 0
	reason := concat(",", sort(pre_deny_reasons))
}

# ---------------------------------------------------------------------------
# post_tool_call — reconciliation / defence in depth
#
# Only fires when a submission actually SUCCEEDED (status "ok") while a violation
# was present, i.e. when the tools' own view and the session's view disagree. This
# is the reconciliation Clarity failure-06 asks for: injected session state is
# checked against the tool-returned validation block rather than trusted blindly.
# A rejected submission is passed straight back so the model can see and report it.
# ---------------------------------------------------------------------------

post_value := input.policy_target.value if {
	is_object(input.policy_target.value)
}

post_value := json.unmarshal(input.policy_target.value) if {
	is_string(input.policy_target.value)
}
post_deny_reasons contains "executed_with_missing_prior_steps" if {
	input.intervention_point == "post_tool_call"
	input.tool.name in control_surfaces
	post_value.status == "ok"
	count(post_value.validation.missing_prior_steps) > 0
}

post_deny_reasons contains "executed_with_sequence_violations" if {
	input.intervention_point == "post_tool_call"
	input.tool.name in control_surfaces
	post_value.status == "ok"
	count(post_value.validation.sequence_violations) > 0
}

post_deny_reasons contains "executed_inside_freeze_without_exception" if {
	input.intervention_point == "post_tool_call"
	input.tool.name in control_surfaces
	post_value.status == "ok"
	post_value.validation.requires_freeze_exception
	post_value.surface in {"rollout_service", "approval"}
	not post_value.freeze_exception_cited
}

# The tools keep state in a shared store, so a prerequisite completed by some other
# session can make a submission succeed that THIS session never earned.
post_deny_reasons contains "executed_with_session_prerequisites_unmet" if {
	input.intervention_point == "post_tool_call"
	input.tool.name in control_surfaces
	post_value.status == "ok"
	count(post_value.session_missing_prior_steps) > 0
}

post_tool_call_verdict := {"decision": "deny", "reason": reason} if {
	input.intervention_point == "post_tool_call"
	count(post_deny_reasons) > 0
	reason := concat(",", sort(post_deny_reasons))
}
