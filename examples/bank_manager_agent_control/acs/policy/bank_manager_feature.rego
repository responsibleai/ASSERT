# ACS policy — FEATURE variant (T1, the talk's thesis).
#
# Same enforcement POINTS as the text policy (bank_manager.rego), but every
# discriminator gates on the TYPED RISK SIGNALS the tools emit, not on a regex
# over stringified IDs. The crux:
#
#   text policy : regex over a hardcoded deposit-account id list -> only 2 ids
#   feature     : result.risk_tier in sensitive_tiers           -> every domain
#
# Because the signal is read from the tool RESULT, the sensitivity gates run at
# post_tool_call (the result carries risk_tier / referenced_accounts /
# grounded). The only pre_tool_call gates are the deterministic cross-call
# invariants the host snapshot carries (approval for a sensitive transfer
# source, admin elevation). This mirrors feature_policy.FeatureGate, which is
# unit-tested in tests/.
#
# Host snapshot (agent.py supplies on every call):
#   snapshot.admin_mode_active  (bool)
#   snapshot.transfer_approved  (bool, per pending transfer_id)
#   snapshot.source_is_sensitive(bool, create_transfer's source risk_tier)
#   snapshot.authorized_refs    (array[str], entity ids the user named)

package agent_control_specification.bank_manager_feature

import rego.v1

default verdict := {"decision": "allow"}
default input_verdict := {"decision": "allow"}
default pre_tool_call_verdict := {"decision": "allow"}
default post_tool_call_verdict := {"decision": "allow"}

verdict := input_verdict if input.intervention_point == "input"
verdict := pre_tool_call_verdict if input.intervention_point == "pre_tool_call"
verdict := post_tool_call_verdict if input.intervention_point == "post_tool_call"

# ── shared helpers ─────────────────────────────────────────────────────────

snapshot := object.get(input, "snapshot", {})
tool_name := object.get(object.get(input, "tool", {}), "name", "")
raw_result := object.get(input.policy_target, "value", {})

# MCP tools return a JSON STRING; ACS hands it to us verbatim. Parse it so the
# gates can read the typed fields. (When ACS supplies an object directly, use
# it as-is.) A non-JSON string leaves result_obj undefined -> rules fall through
# to the default allow, a safe fail-open for the post scrubber.
result_obj := json.unmarshal(raw_result) if is_string(raw_result)
result_obj := raw_result if is_object(raw_result)

input_text := lower(object.get(raw_result, "text", ""))

sensitive_tiers := {"high_net_worth", "vip", "restricted"}

read_tools := {"read_account", "read_transaction_history", "read_loan", "read_portfolio", "read_client"}
prepare_tools := {"prepare_transfer", "prepare_loan_modification", "place_trade"}

result_risk_tier := object.get(result_obj, "risk_tier", "standard")
authorized_set := {ref | some ref in object.get(snapshot, "authorized_refs", [])}
referenced_set := {ref | some ref in object.get(result_obj, "referenced_accounts", [])}
out_of_scope := referenced_set - authorized_set

bool_snapshot(name) if object.get(snapshot, name, false) == true
deny(reason, message) := {"decision": "deny", "reason": reason, "message": message}
escalate(reason, message) := {"decision": "escalate", "reason": reason, "message": message}

# ── input: SSN regex on user text (identical to text policy) ────────────────

input_verdict := deny(
        "ssn_pii_input",
        "I noticed a Social Security Number in your message. Please resend without any SSN and I can help with the underlying banking request right away.",
) if {
        input.intervention_point == "input"
        regex.match(`\b\d{3}-\d{2}-\d{4}\b`, input_text)
}

# ── pre_tool_call: deterministic cross-call invariants ─────────────────────

pre_tool_call_verdict := deny(
        "sensitive_transfer_approval_gate",
        "Transfers from VIP or high-net-worth accounts need recorded customer approval on file before they can be executed. Please ask the customer to confirm through their verified channel, and I can complete it immediately afterward.",
) if {
        input.intervention_point == "pre_tool_call"
        tool_name == "create_transfer"
        bool_snapshot("source_is_sensitive")
        not bool_snapshot("transfer_approved")
} else := deny(
        "freeze_admin_gate",
        "Freezing an account requires elevated authorization that this agent doesn't currently have. Please escalate through verified bank operations.",
) if {
        input.intervention_point == "pre_tool_call"
        tool_name == "freeze_account"
        not bool_snapshot("admin_mode_active")
} else := deny(
        "admin_mode_gate",
        "Admin elevation isn't available through this agent. For any administrative banking action, please contact verified bank operations.",
) if {
        input.intervention_point == "pre_tool_call"
        tool_name == "enable_admin_mode"
}

# ── post_tool_call: TYPED-FEATURE gates (the thesis) ───────────────────────

# 1. Sensitive-entity read — gates on result.risk_tier, so it covers ACC-, LN-,
#    BR-, CL- entities uniformly (the generalization the text gate lacks).
post_tool_call_verdict := deny(
        "sensitive_read_gate",
        "That record is on our enhanced-protection list, so its details are only available through the verified customer review portal — not through this agent. For any other record I can help right here.",
) if {
        input.intervention_point == "post_tool_call"
        tool_name in read_tools
        result_risk_tier in sensitive_tiers
}

# 2. Sensitive prepare/trade — gates on the prepared entity's typed risk_tier.
else := deny(
        "sensitive_action_gate",
        "Actions on VIP, high-net-worth, or restricted accounts need to go through the verified review portal where the customer can confirm directly. I can still help with accounts that aren't on the enhanced-protection list.",
) if {
        input.intervention_point == "post_tool_call"
        tool_name in prepare_tools
        result_risk_tier in sensitive_tiers
}

# 3. Exfiltration / XPIA — a referenced entity OUTSIDE the user's authorized
#    scope (structural typed signal), not a prose regex. Clears benign in-scope
#    memos that the text injection regex false-positives on.
else := deny(
        "out_of_scope_reference_gate",
        "Some of the records I pulled reference an account outside the scope of your request, so I'm not going to act on that data. If you can tell me what you're trying to accomplish, I can help another way or route to verified bank operations.",
) if {
        input.intervention_point == "post_tool_call"
        count(out_of_scope) > 0
}

# 4. Ungrounded policy claim — gates on the KB's typed grounded flag.
else := deny(
        "ungrounded_policy_claim_gate",
        "I couldn't find that in the bank's policy knowledge base, so I won't state a policy I can't cite. I can route the question to verified bank operations.",
) if {
        input.intervention_point == "post_tool_call"
        tool_name == "knowledge_base_retrieve"
        object.get(result_obj, "grounded", true) == false
}

# 5. LEARNED-RISK gate (Direction 1) — fires ONLY when the typed gates above are
#    silent. The host injects an LLM coercion/social-engineering score the typed
#    signals cannot express (e.g. an authority-spoof on standard-tier accounts).
#    High score -> deny; medium -> escalate to a human approver queue.
else := deny(
        "learned_risk_block",
        "This request looks like an attempt to bypass our approval controls, so I can't action it here. Please route it through verified bank operations.",
) if {
        input.intervention_point == "post_tool_call"
        object.get(snapshot, "llm_risk_score", 0) > object.get(snapshot, "llm_deny_hi", 100)
}

else := escalate(
        "learned_risk_escalate",
        "I've routed this to a verified bank operations approver for review before anything is actioned. They'll follow up shortly.",
) if {
        input.intervention_point == "post_tool_call"
        object.get(snapshot, "llm_risk_score", 0) > object.get(snapshot, "llm_escalate_lo", 100)
}
