# ACS policy — sensitivity-tier authorization.
#
# THE ENTIRE POLICY IS ONE RULE. Read it and note what is NOT in it:
#
#   * no tool names          -> a new tool is covered the day it ships
#   * no ID prefixes         -> ACC-, LN-, BR-, CL-, and INS-/TR- domains that
#                               do not exist yet are all covered identically
#   * no per-domain field    -> it reads the platform-normalized `risk_tier`
#   * no entity allow-list   -> it keys on the PROPERTY, not on an ID
#
# That is the claim under test: written once, against a property, it holds for
# every domain the platform emits the property for — with zero new code.
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
#
# Fail-closed note: unlike the AIEWF demo policy, an unparseable result does NOT
# fall through to allow — see `unreadable_result` at the bottom.

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

snapshot := object.get(input, "snapshot", {})

authorized := {e | some e in object.get(snapshot, "authorized_entities", [])}

raw_result := object.get(object.get(input, "policy_target", {}), "value", {})

result_obj := json.unmarshal(raw_result) if is_string(raw_result)

result_obj := raw_result if is_object(raw_result)

subject := object.get(result_obj, "entity_id", "")

subject_tier := object.get(result_obj, "risk_tier", "standard")

deny(reason, message) := {"decision": "deny", "reason": reason, "message": message}

# ── THE RULE ───────────────────────────────────────────────────────────────
# A result describing a protected entity may not reach the model unless a
# verified authorization record exists for that entity.

post_tool_call_verdict := deny(
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

protected_refs := {e | some e in object.get(snapshot, "protected_refs", [])}

pre_tool_call_verdict := deny(
	"tier_authorization_required_precall",
	sprintf("That action targets a protected record (%v) with no verified authorization on file. Call verify_authorization first and I can action it right away.", [concat(", ", sort(protected_refs - authorized))]),
) if {
	input.intervention_point == "pre_tool_call"
	object.get(snapshot, "state_changing", false) == true
	count(protected_refs - authorized) > 0
}

# ── fail closed ────────────────────────────────────────────────────────────
# The AIEWF policy fell through to `allow` when a tool result could not be
# parsed (documented there as a demo-only fail-open). Here an unreadable result
# is treated as unclassified and denied, because the classification is exactly
# what the rule depends on.

unreadable_result if {
	is_string(raw_result)
	not json.is_valid(raw_result)
}

post_tool_call_verdict := deny(
	"unclassified_result",
	"I couldn't confirm the sensitivity classification of that record, so I'm not going to disclose it. Please retry, or route the request to verified bank operations.",
) if {
	input.intervention_point == "post_tool_call"
	unreadable_result
}
