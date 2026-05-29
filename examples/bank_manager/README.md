# Bank Manager Agent Shield example

This example is an A/B before/after-guardrails demo for a LangGraph banking agent connected to a mock MCP server.

| Variant | Config | Callable | Story |
|---|---|---|---|
| A · unguarded | `eval_config_unguarded.yaml` | `examples.bank_manager.agent:chat_unguarded` | Baseline agent with no ACS gates. |
| B · guarded | `eval_config_guarded.yaml` | `examples.bank_manager.agent:chat_guarded` | Same agent and same eval, wrapped with minimal deterministic ACS gates. |

Variant B does not change the system prompt. The only intended difference is the ACS policy loaded from `guardrails.yaml`.

## Failure-mode taxonomy

The eval spec uses four axes:

| Axis | What can go wrong | Representative `request_type` values | Minimal deterministic guardrail |
|---|---|---|---|
| Instructions & Controls | The agent skips required control flow or executes an unauthorized action. | `vip_transfer_requires_approval`, `freeze_account_requires_admin_mode` | State-machine gates for VIP transfer preparation, transfer approval before execution, and admin mode before account freeze. |
| Information integrity | The agent invents transfer state, IDs, approval tokens, or outcomes not grounded in tool returns. | `vip_transfer_requires_approval`, `ceo_fraud_social_engineering` | Transfer IDs and acknowledgement tokens must be emitted by the prior tool in the prepare -> approval -> create chain. |
| Tool/Action Misuse | The agent leaks sensitive identifiers or PII into assistant-visible text. | `ssn_in_input`, `account_enumeration_probe` | Regex input blocks for SSNs and account-enumeration probes plus deterministic PII redaction on tool output. |
| System-level Emergent | The agent follows social pressure, fake authority, or instructions embedded in retrieved transaction data. | `ceo_fraud_social_engineering`, `prompt_injection_in_memo` | Authority-plus-bypass regex and a transaction-memo account-reference gate before `prepare_transfer`. |

The stratification dimensions are identical across A and B: `request_type`, `pressure`, and `pressure_escalation_intensity`. This keeps the comparison focused on whether the minimal deterministic guardrails reduce policy violations without adding overrefusal on benign account lookups.

## Run the two variants

From the repository root:

```powershell
python -m pip install -e ".[otel,langgraph]"
p2m run --config examples/bank_manager/eval_config_unguarded.yaml
p2m run --config examples/bank_manager/eval_config_guarded.yaml
```

Required environment variable names are `AZURE_API_KEY`, `AZURE_API_BASE`, and optionally `AZURE_API_VERSION` / `AGENT_MODEL`. Do not commit local `.env` files or generated artifacts.

## Files

- `agent.py` exposes `chat_unguarded` and `chat_guarded`.
- `mcp_server.py` provides the mock banking tools and sample data.
- `guardrails.yaml` contains the minimal deterministic ACS policy used by variant B.
- `bank-base.guardrails.yaml` and `bank-mcp-template.guardrails.yaml` are retained as reference templates but are not loaded by `chat_guarded`.

Committed result snapshots were intentionally removed because the configs changed to `sample_size: 30` with default judge dimensions. Re-run the two configs to regenerate local artifacts when needed.
