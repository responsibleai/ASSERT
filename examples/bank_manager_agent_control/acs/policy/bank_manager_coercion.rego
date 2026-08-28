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
classifier_provenance := object.get(coercion, "classifier_provenance", null)

tool_name := object.get(object.get(input, "tool", {}), "name", "")
snapshot := object.get(input, "snapshot", {})
current_action_binding := object.get(snapshot, "current_action_binding", null)
current_session_id := object.get(snapshot, "control_session_id", null)
current_target := object.get(object.get(input, "policy_target", {}), "value", {})

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

amount_valid(amount) if {
	amount == null
}

amount_valid(amount) if {
	is_number(amount)
}

action_binding_valid(binding) if {
	is_object(binding)
	is_string(object.get(binding, "action_family", null))
	is_string(object.get(binding, "tool_name", null))
	is_string(object.get(binding, "action_instance", null))
	is_string(object.get(binding, "subject", null))
	is_string(object.get(binding, "to_account", null))
	is_string(object.get(binding, "destination", null))
	object.get(binding, "destination", null) == object.get(binding, "to_account", null)
	amount := object.get(binding, "amount", null)
	amount_valid(amount)
	is_object(object.get(binding, "parameters", null))
	session_id := object.get(binding, "session_id", "")
	is_string(session_id)
	session_id != ""
	message_sha256 := object.get(binding, "message_sha256", "")
	is_string(message_sha256)
	regex.match("^sha256:[0-9a-f]{64}$", message_sha256)
}

verified_action_binding_complete(binding) if {
	object.get(binding, "action_family", "") == "transfer"
	object.get(binding, "subject", "") != ""
	object.get(binding, "to_account", "") != ""
	amount := object.get(binding, "amount", null)
	is_number(amount)
	amount > 0
}

verified_action_binding_complete(binding) if {
	object.get(binding, "action_family", "") == "loan_modification"
	object.get(binding, "subject", "") != ""
	object.get(binding, "to_account", null) == ""
	object.get(binding, "amount", "invalid") == null
}

target_action_instance := object.get(current_target, "transfer_id", null) if {
	tool_name in {"create_transfer", "request_customer_approval"}
}

target_action_instance := object.get(current_target, "loan_id", null) if {
	tool_name == "prepare_loan_modification"
}

target_action_instance := object.get(current_target, "portfolio_id", null) if {
	tool_name == "place_trade"
}

target_action_instance := object.get(current_target, "account_id", null) if {
	tool_name == "freeze_account"
}

current_binding_message_matches if {
	message := object.get(snapshot, "user_message", null)
	is_string(message)
	object.get(current_action_binding, "message_sha256", null) == sprintf("sha256:%s", [crypto.sha256(message)])
}

verified_binding_matches_current_call if {
	action_binding := object.get(artifact_evidence, "action_context", null)
	action_binding_valid(action_binding)
	action_binding_valid(current_action_binding)
	verified_action_binding_complete(action_binding)
	verified_action_binding_complete(current_action_binding)
	action_binding == current_action_binding
	is_string(current_session_id)
	current_session_id != ""
	object.get(artifact_evidence, "session_id", null) == current_session_id
	object.get(current_action_binding, "session_id", null) == current_session_id
	object.get(current_action_binding, "tool_name", "") == tool_name
	object.get(current_action_binding, "action_instance", null) == target_action_instance
	object.get(current_action_binding, "parameters", null) == current_target
	tool_name in {"create_transfer", "request_customer_approval"}
	current_binding_message_matches
}

verified_binding_matches_current_call if {
	action_binding := object.get(artifact_evidence, "action_context", null)
	action_binding_valid(action_binding)
	action_binding_valid(current_action_binding)
	verified_action_binding_complete(action_binding)
	verified_action_binding_complete(current_action_binding)
	action_binding == current_action_binding
	is_string(current_session_id)
	current_session_id != ""
	object.get(artifact_evidence, "session_id", null) == current_session_id
	object.get(current_action_binding, "session_id", null) == current_session_id
	object.get(current_action_binding, "tool_name", "") == tool_name
	object.get(current_action_binding, "action_instance", null) == target_action_instance
	object.get(current_action_binding, "subject", null) == target_action_instance
	object.get(current_action_binding, "parameters", null) == current_target
	tool_name in {"prepare_loan_modification", "place_trade", "freeze_account"}
	current_binding_message_matches
}

artifact_binding_consistent if {
	artifact_verified == false
}

artifact_binding_consistent if {
	artifact_verified == true
	verified_binding_matches_current_call
}

artifact_evidence_valid if {
	is_object(artifact_evidence)
	is_string(object.get(artifact_evidence, "session_id", null))
	object.get(artifact_evidence, "session_id", "") != ""
	action_context := object.get(artifact_evidence, "action_context", null)
	action_binding_valid(action_context)
	is_object(matched_action_instances)
	every _, action_ids in matched_action_instances {
		string_array(action_ids)
		count(action_ids) > 0
	}
}

classifier_provenance_valid if {
	is_object(classifier_provenance)
	is_string(object.get(classifier_provenance, "classifier_mode", null))
	object.get(classifier_provenance, "classifier_mode", "") in {"live_model", "injected_scorer"}
	is_boolean(object.get(classifier_provenance, "classifier_invoked", null))
	is_string(object.get(classifier_provenance, "classifier_deployment", null))
	object.get(classifier_provenance, "classifier_deployment", "") != ""
	is_string(object.get(classifier_provenance, "calibration_model", null))
	is_string(object.get(classifier_provenance, "calibration_artifact", null))
	object.get(classifier_provenance, "calibration_artifact", "") != ""
	hash := object.get(classifier_provenance, "calibration_sha256", "")
	is_string(hash)
	regex.match("^sha256:[0-9a-f]{64}$", hash)
	is_number(object.get(classifier_provenance, "calibration_schema_version", null))
	is_string(object.get(classifier_provenance, "threshold_version", null))
	object.get(classifier_provenance, "threshold_version", "") != ""
	object.get(classifier_provenance, "escalate_lo", null) == escalate_lo
	object.get(classifier_provenance, "deny_hi", null) == deny_hi
}

reference_sets_consistent if {
	verified := {ref | some ref in verified_references}
	cited := {ref | some ref in cited_references}
	count(verified - cited) == 0
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
	classifier_provenance_valid
	artifact_verification_consistent
	artifact_binding_consistent
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
