# ACS policy — coercion via unverified authority claims (learned-gate tier).
#
# The typed-feature policy (bank_manager_feature.rego) is the deterministic tier:
# risk_tier, referenced_accounts, grounded, transfer_approved. This file is the
# LEARNED tier, and it deliberately owns only the residual the typed tier cannot
# express.
#
# The gate reads a host-produced annotation, NOT a model. ACS §10: the runtime
# resolves each `from` path, calls the host annotator dispatcher, and places the
# output only under `annotations.<name>`. Policy execution stays pure and
# deterministic given that input — which is what makes the decision auditable
# and replayable even though the signal upstream of it is learned.
#
# Three-valued on purpose. A learned signal has a genuine uncertain middle, and
# collapsing it to allow/deny throws away the one action that costs least when
# the classifier is unsure: route it to a human approver.
#
#   score > deny_hi      -> deny     (clear coercion; do not action here)
#   score > escalate_lo  -> escalate (route to the verified-operations queue)
#   otherwise            -> allow
#
# Both band edges come from the annotation itself (`escalate_lo` / `deny_hi`),
# so recalibrating the classifier re-tunes the gate without editing Rego. The
# defaults below are intentionally unreachable (>1.0) so a MISSING annotation
# can never silently widen the allow band.

package agent_control_specification.bank_manager_coercion

import rego.v1

default verdict := {"decision": "allow"}
default pre_tool_call_verdict := {"decision": "allow"}
default post_tool_call_verdict := {"decision": "allow"}

verdict := pre_tool_call_verdict if input.intervention_point == "pre_tool_call"
verdict := post_tool_call_verdict if input.intervention_point == "post_tool_call"

annotations := object.get(input, "annotations", {})
coercion := object.get(annotations, "coercion_risk", {})
score := object.get(coercion, "score", 0)
escalate_lo := object.get(coercion, "escalate_lo", 2)
deny_hi := object.get(coercion, "deny_hi", 2)
invalid_references := object.get(coercion, "invalid_references", [])
verified_references := object.get(coercion, "verified_references", [])
artifact_verified := object.get(coercion, "artifact_verified", null)
cited_references := object.get(coercion, "cited_references", [])
label := object.get(coercion, "label", null)
raw_annotation := object.get(coercion, "raw", null)
artifact_evidence := object.get(coercion, "artifact_verification", null)
matched_action_instances := object.get(artifact_evidence, "matched_action_instance_ids", null)

tool_name := object.get(object.get(input, "tool", {}), "name", "")

# Only actions where skipping a recorded control step actually changes state.
# Reads stay ungated: an over-broad learned gate is how you buy back the
# over-refusal you were trying to remove.
gated_tools := {
	"create_transfer",
	"request_customer_approval",
	"prepare_loan_modification",
	"place_trade",
	"freeze_account",
	"enable_admin_mode",
}

deny(reason, message) := {"decision": "deny", "reason": reason, "message": message}

escalate(reason, message) := {"decision": "escalate", "reason": reason, "message": message}

# ── pre_tool_call: the learned coercion gate ───────────────────────────────

string_array(value) if {
	is_array(value)
	every item in value {
		is_string(item)
	}
}

artifact_verification_consistent if {
	artifact_verified == false
	count(verified_references) == 0
	count(matched_action_instances) == 0
	label != "recorded_artifact_verified"
}

artifact_verification_consistent if {
	artifact_verified == true
	count(verified_references) > 0
	count(invalid_references) == 0
	object.keys(matched_action_instances) == {ref | some ref in verified_references}
	label == "recorded_artifact_verified"
}

artifact_evidence_valid if {
	is_object(artifact_evidence)
	is_string(object.get(artifact_evidence, "session_id", null))
	object.get(artifact_evidence, "session_id", "") != ""
	action_context := object.get(artifact_evidence, "action_context", null)
	is_object(action_context)
	is_string(object.get(action_context, "action_family", null))
	is_string(object.get(action_context, "tool_name", null))
	is_string(object.get(action_context, "action_instance", null))
	is_string(object.get(action_context, "subject", null))
	is_object(matched_action_instances)
	every _, action_ids in matched_action_instances {
		string_array(action_ids)
		count(action_ids) > 0
	}
}

reference_sets_consistent if {
	verified := {ref | some ref in verified_references}
	invalid := {ref | some ref in invalid_references}
	cited := {ref | some ref in cited_references}
	count(verified - cited) == 0
	count(invalid - cited) == 0
}

annotation_valid if {
	object.get(annotations, "coercion_risk", null) != null
	is_object(coercion)
	label in {"clean", "ambiguous", "coercive", "recorded_artifact_verified"}
	is_object(raw_annotation)
	is_number(score)
	score >= 0
	score <= 1
	is_number(escalate_lo)
	escalate_lo >= 0
	is_number(deny_hi)
	escalate_lo < deny_hi
	deny_hi <= 1
	is_boolean(artifact_verified)
	string_array(cited_references)
	string_array(verified_references)
	string_array(invalid_references)
	artifact_evidence_valid
	artifact_verification_consistent
	reference_sets_consistent
}

pre_tool_call_verdict := escalate(
	"coercion_annotation_missing",
	"I couldn't verify the learned control signal for this action, so I routed it to verified bank operations instead of executing it.",
) if {
	input.intervention_point == "pre_tool_call"
	tool_name in gated_tools
	not annotation_valid
} else := escalate(
	"unverified_control_artifact",
	"The request cites a control reference that the bank's records could not verify for this action. I routed it to verified bank operations instead of treating the string as authorization.",
) if {
	input.intervention_point == "pre_tool_call"
	tool_name in gated_tools
	count(invalid_references) > 0
} else := deny(
	"coercion_unverified_authority",
	"I can't action that here. The request asks me to stand in a claimed authority or an unrecorded verbal assurance in place of the recorded approval step, and that step is what protects the customer. Send it to verified bank operations with an authorization reference and I can complete it immediately.",
) if {
	input.intervention_point == "pre_tool_call"
	tool_name in gated_tools
	score > deny_hi
} else := escalate(
	"coercion_unverified_authority_ambiguous",
	"I've routed this to the verified bank operations approver queue for a second look before anything is actioned — I couldn't confirm from the request whether a recorded authorization is already on file. If you have the authorization or callback reference, send it and I can complete it right away.",
) if {
	input.intervention_point == "pre_tool_call"
	tool_name in gated_tools
	score > escalate_lo
}

# ── post_tool_call: nothing learned runs here ──────────────────────────────
#
# The typed post-call scrubbers live in bank_manager_feature.rego. Keeping this
# point declared but silent makes the manifest complete without duplicating a
# deterministic rule into the learned tier.
