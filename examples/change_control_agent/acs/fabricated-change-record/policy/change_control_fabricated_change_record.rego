package agent_control_specification.change_control_fabricated_change_record

# Fabricated change record (Clarity failure-02) — a STRUCTURAL gate.
#
# create_change_request returns a deterministic field_provenance map and a
# fabricated_fields list naming every value the source proposal does not support.
# That comparison does not exist until the tool has run, so the check is POST-call
# by construction — the arguments alone cannot reveal fabrication.
#
# The policy CONSUMES the tool's own provenance output; it does not re-implement
# the provenance rules, so it cannot drift from the tools.
#
# The control surfaces are additionally held PRE-call while a record with
# unsupported values is still outstanding, so a false record cannot be carried
# downstream. That rule reads a scalar the governed agent injects from the LAST
# OBSERVED create_change_request RESULT for the change — never from the model's
# narration — and it clears itself as soon as a corrected record comes back with
# fabricated_fields: [].
#
# Deny reasons are collected in a partial set and joined into ONE verdict so two
# simultaneous conditions can never become an evaluation conflict.

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

record_tool := "create_change_request"

control_surfaces := {
	"submit_to_deployment_gateway",
	"submit_to_rollout_service",
	"submit_to_release_readiness",
	"route_for_approval",
}

# ---------------------------------------------------------------------------
# pre_tool_call — block downstream advancement while a false record stands.
#
# create_change_request is deliberately NOT gated here: the corrected resubmission
# is the remediation path and must always be able to run.
# ---------------------------------------------------------------------------

pre_deny_reasons contains "fabricated_record_outstanding" if {
	input.intervention_point == "pre_tool_call"
	input.tool.name in control_surfaces
	count(input.policy_target.value.outstanding_fabricated_fields) > 0
}

pre_tool_call_verdict := {"decision": "deny", "reason": reason} if {
	input.intervention_point == "pre_tool_call"
	count(pre_deny_reasons) > 0
	reason := concat(",", sort(pre_deny_reasons))
}

# ---------------------------------------------------------------------------
# post_tool_call — the provenance gate itself.
#
# The host injects the tool result as an object; ``assert-ai acs validate``
# replays the same result as a JSON string. Accept both so the rule is
# exercised offline as well as in production.
# ---------------------------------------------------------------------------

post_value := input.policy_target.value if {
	is_object(input.policy_target.value)
}

post_value := json.unmarshal(input.policy_target.value) if {
	is_string(input.policy_target.value)
}

post_deny_reasons contains "fabricated_change_record" if {
	input.intervention_point == "post_tool_call"
	input.tool.name == record_tool
	count(post_value.validation.fabricated_fields) > 0
}

post_deny_reasons contains "fabricated_change_record" if {
	input.intervention_point == "post_tool_call"
	input.tool.name == record_tool
	count(post_value.fabricated_fields) > 0
}

post_tool_call_verdict := {"decision": "deny", "reason": reason} if {
	input.intervention_point == "post_tool_call"
	count(post_deny_reasons) > 0
	reason := concat(",", sort(post_deny_reasons))
}

# ---------------------------------------------------------------------------
# output — the semantic residual (Shape 4).
#
# The structural post gate only sees calls that were made. Baseline evidence:
# 12 of 20 harmful rows never called create_change_request at all and instead
# presented invented impact / blast radius / owner / rollback values as fact in
# the reply. The host annotator judges the reply against the proposal facts it
# observed from tool results; `== true` fails OPEN when the annotator did not run.
# ---------------------------------------------------------------------------
