# ACS policy — sensitivity-tier authorization.
#
# The authorization predicate is one property rule, preceded by fail-closed
# validation of the host snapshot and result envelope. Note what the predicate
# does NOT contain:
#
#   * no tool-name predicate -> native dispatch registration stays in manifest
#   * no ID prefixes         -> ACC-, LN-, BR-, CL-, and INS-/TR- domains that
#                               emit the contract are evaluated identically
#   * no per-domain field    -> it reads the platform-normalized `risk_tier`
#   * no entity allow-list   -> it keys on the PROPERTY, not on an ID
#
# That is the claim under test: given a trusted normalized envelope, the
# authorization predicate is domain-independent. Host integration for a new
# domain remains separate work and is not proved by direct Rego inputs.
#
# Contract with the host (agent_tier_authz.py):
#   input.policy_target.value  the tool RESULT (JSON string or object), carrying
#                              the platform sensitivity envelope
#                              {entity_id, risk_tier, ...}
#   input.snapshot.authorized_entities
#                              entity IDs with a verified authorization record
#                              recorded this session
#   input.snapshot.call_refs   ID-shaped values in the tool ARGUMENTS (used by
#                              the pre-call gate for state-changing calls)
#   input.snapshot.protected_refs
#                              of those, the ones the platform authoritatively
#                              resolves to a protected tier before execution
#   input.snapshot.unresolved_refs
#                              referenced entities with missing/invalid tiers;
#                              state-changing calls fail closed before execution
#
# Missing, malformed, or unknown snapshot/result fields deny before the
# authorization predicate is evaluated.

package agent_control_specification.tier_authorization

import rego.v1

default verdict := {"decision": "allow"}
default input_verdict := {"decision": "allow"}
default pre_tool_call_verdict := {"decision": "allow"}
default post_tool_call_verdict := {"decision": "allow"}

verdict := input_verdict if input.intervention_point == "input"

verdict := pre_tool_call_verdict if input.intervention_point == "pre_tool_call"

verdict := post_tool_call_verdict if input.intervention_point == "post_tool_call"

# ── the property ───────────────────────────────────────────────────────────

sensitive_tiers := {"high_net_worth", "vip", "restricted"}
risk_tiers := {"standard", "high_net_worth", "vip", "restricted"}

snapshot := object.get(input, "snapshot", {})

authorized_entities := object.get(snapshot, "authorized_entities", null)
call_refs := object.get(snapshot, "call_refs", null)
protected_ref_values := object.get(snapshot, "protected_refs", null)
unresolved_ref_values := object.get(snapshot, "unresolved_refs", null)
state_changing := object.get(snapshot, "state_changing", null)

string_array(value) if {
	is_array(value)
	every item in value {
		is_string(item)
	}
}

post_snapshot_valid if {
	is_object(snapshot)
	string_array(authorized_entities)
}

pre_snapshot_valid if {
	post_snapshot_valid
	string_array(call_refs)
	string_array(protected_ref_values)
	string_array(unresolved_ref_values)
	is_boolean(state_changing)
}

authorized := {e | some e in authorized_entities}

raw_result := object.get(object.get(input, "policy_target", {}), "value", {})

result_obj := json.unmarshal(raw_result) if is_string(raw_result)

result_obj := raw_result if is_object(raw_result)

subject := object.get(result_obj, "entity_id", null)

subject_tier := object.get(result_obj, "risk_tier", null)

deny(reason, message) := {"decision": "deny", "reason": reason, "message": message}

result_valid if {
	is_object(result_obj)
	is_string(subject)
	subject != ""
	is_string(subject_tier)
	subject_tier in risk_tiers
}

# ── THE RULE ───────────────────────────────────────────────────────────────
# A result describing a protected entity may not reach the model unless a
# verified authorization record exists for that entity.

post_tool_call_verdict := deny(
	"invalid_control_input",
	"I couldn't validate the authorization snapshot, so I denied the result instead of widening access.",
) if {
	input.intervention_point == "post_tool_call"
	not post_snapshot_valid
} else := deny(
	"unclassified_result",
	"I couldn't confirm the sensitivity classification of that record, so I'm not going to disclose it. Please retry, or route the request to verified bank operations.",
) if {
	input.intervention_point == "post_tool_call"
	not result_valid
} else := deny(
	"tier_authorization_required",
	sprintf("Record %s is classified '%s'. Reads and actions on protected records require a verified customer authorization on file. Call verify_authorization('%s', <justification>) first, then retry — I can complete it immediately afterwards.", [subject, subject_tier, subject]),
) if {
	input.intervention_point == "post_tool_call"
	subject_tier in sensitive_tiers
	not subject in authorized
}

# ── same rule, applied one step earlier ────────────────────────────────────
# A state-changing call whose arguments name an entity the platform resolves to
# a protected tier is stopped BEFORE it executes, so the write never happens.
# Same predicate, same property, no tool names: "state-changing" is the host's
# own tool metadata, and `protected_refs` is derived by the platform from its
# authoritative `risk_tier` registry, not from an ID allow-list.

protected_refs := {e | some e in protected_ref_values}
unresolved_refs := {e | some e in unresolved_ref_values}

pre_tool_call_verdict := deny(
	"invalid_control_input",
	"I couldn't validate the authorization snapshot, so I denied the call instead of executing with incomplete control data.",
) if {
	input.intervention_point == "pre_tool_call"
	not pre_snapshot_valid
} else := deny(
	"unresolved_tier_precall",
	sprintf("I couldn't resolve a valid risk tier for %v, so I denied the state-changing call before execution.", [concat(", ", sort(unresolved_refs))]),
) if {
	input.intervention_point == "pre_tool_call"
	state_changing == true
	count(unresolved_refs) > 0
} else := deny(
	"tier_authorization_required_precall",
	sprintf("That action targets a protected record (%v) with no verified authorization on file. Call verify_authorization first and I can action it right away.", [concat(", ", sort(protected_refs - authorized))]),
) if {
	input.intervention_point == "pre_tool_call"
	state_changing == true
	count(protected_refs - authorized) > 0
}
