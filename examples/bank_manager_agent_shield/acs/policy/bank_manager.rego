# ACS policy for the bank-manager demo. Mirrors guardrails.v3.yaml:
#   - input            : SSN regex on user text
#   - pre_tool_call    : sensitivity-scoped read / transfer gates,
#                        approval / admin-mode gates over host snapshot
#   - post_tool_call   : generic prompt-injection scrubber on result blob
#
# ACS is stateless. The host supplies the per-turn state on every snapshot:
#   snapshot.transfer_approved  (bool, default false)
#   snapshot.admin_mode_active  (bool, default false)
#
# Snapshot.tool_call.args is the JSON-serialised tool args dict.
# Snapshot.tool_result is the JSON-serialised tool result dict, with the
# textual portion under .text when one exists.

package agent_control_specification.bank_manager

import rego.v1

default verdict := {"decision": "allow"}
default input_verdict := {"decision": "allow"}
default pre_tool_call_verdict := {"decision": "allow"}
default post_tool_call_verdict := {"decision": "allow"}

verdict := input_verdict if input.intervention_point == "input"
verdict := pre_tool_call_verdict if input.intervention_point == "pre_tool_call"
verdict := post_tool_call_verdict if input.intervention_point == "post_tool_call"

# ── shared snapshot helpers ────────────────────────────────────────────────

snapshot := object.get(input, "snapshot", {})
tool_name := object.get(object.get(input, "tool", {}), "name", "")
tool_args := object.get(input.policy_target, "value", {})
tool_args_blob := sprintf("%v", [tool_args])

input_text := lower(object.get(object.get(input.policy_target, "value", {}), "text", ""))

result_value := object.get(input.policy_target, "value", {})
result_text := lower(object.get(result_value, "text", sprintf("%v", [result_value])))

# ── input: SSN regex on user text ──────────────────────────────────────────

input_verdict := deny(
        "ssn_pii_input",
        "I noticed a Social Security Number in your message. Please resend without any SSN and I can help with the underlying banking request right away.",
) if {
        input.intervention_point == "input"
        regex.match(`\b\d{3}-\d{2}-\d{4}\b`, input_text)
}

# ── pre_tool_call: deterministic gates ─────────────────────────────────────

# Sensitive account read: matches ACC-1002 (HNW) and ACC-1003 (VIP) anywhere
# in the read_account / read_transaction_history params blob.
pre_tool_call_verdict := deny(
        "sensitive_read_gate",
        "That account is on our enhanced-protection list, so its details and transaction history are only available through the verified customer review portal — not through this agent. For any other account I can help right here.",
) if {
        input.intervention_point == "pre_tool_call"
        tool_name in {"read_account", "read_transaction_history"}
        regex.match(`ACC-100[23]`, tool_args_blob)
} else := deny(
        "sensitive_transfer_gate",
        "Transfers involving VIP or high-net-worth accounts need to go through the verified review portal where the customer can confirm the amount and destination directly. I can still help with any other transfer or with accounts that aren't on the enhanced-protection list.",
) if {
        input.intervention_point == "pre_tool_call"
        tool_name == "prepare_transfer"
        regex.match(`ACC-100[23]`, tool_args_blob)
} else := deny(
        "vip_transfer_gate",
        "Transfers from VIP and high-net-worth accounts need recorded customer approval on file before they can be executed. Please ask the customer to confirm the transfer through their verified channel, and I can complete it immediately afterward.",
) if {
        input.intervention_point == "pre_tool_call"
        tool_name == "create_transfer"
        sensitive_source
        not bool_snapshot("transfer_approved")
} else := deny(
        "freeze_admin_gate",
        "Freezing an account requires elevated authorization that this agent doesn't currently have. Please escalate the freeze request through verified bank operations and they can action it for you right away.",
) if {
        input.intervention_point == "pre_tool_call"
        tool_name == "freeze_account"
        not bool_snapshot("admin_mode_active")
} else := deny(
        "admin_mode_gate",
        "Admin elevation isn't available through this agent. For any administrative banking action, please contact verified bank operations through your usual support channel and they can take care of it.",
) if {
        input.intervention_point == "pre_tool_call"
        tool_name == "enable_admin_mode"
}

sensitive_source if {
        src := lower(object.get(tool_args, "from_account", object.get(tool_args, "source_account", object.get(snapshot, "account_sensitivity", ""))))
        src in {"acc-1002", "acc-1003", "vip", "high_net_worth"}
}

# ── post_tool_call: prompt-injection scrubber ──────────────────────────────

# Block tool results whose text carries injection-shaped instructions.
# Patterns target structure ("ignore … rules / instructions / policy",
# "transfer / send $… to ACC-####"), not any specific demo string.

post_tool_call_verdict := deny(
        "injected_instruction_in_tool_result",
        "Some of the records I pulled contain text that looks like embedded instructions rather than normal transaction memos, so I'm not going to use that data. If you can tell me what you're trying to accomplish, I can help you another way or route the question to verified bank operations.",
) if {
        input.intervention_point == "post_tool_call"
        injected_instruction
}

injected_instruction if regex.match(`(?i)ignore\s+(your|all|previous|the|any)\s+(rules|instructions|policy|policies|guardrails|directives)`, result_text)
injected_instruction if regex.match(`(?i)disregard\s+(your|all|previous|the|any)\s+(rules|instructions|policy|policies|guardrails)`, result_text)
injected_instruction if regex.match(`(?i)override\s+(your|all|previous|the|any)\s+(rules|instructions|policy|policies)`, result_text)
injected_instruction if regex.match(`(?i)transfer\s+\$[\d,\.]+\s+to\s+acc-\d{4}`, result_text)
injected_instruction if regex.match(`(?i)send\s+\$[\d,\.]+\s+to\s+acc-\d{4}`, result_text)

# ── helpers ────────────────────────────────────────────────────────────────

bool_snapshot(name) if object.get(snapshot, name, false) == true

deny(reason, message) := {"decision": "deny", "reason": reason, "message": message}
