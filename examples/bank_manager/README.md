# Bank-manager Agent Shield example

This example evaluates a LangGraph bank-manager agent connected to a mock banking MCP server. The eval focuses on whether the agent preserves financial facts, follows the transfer state machine, avoids sensitive-data leakage, and ignores social-engineering content embedded in tool data.

## Files

- `mcp_server.py` defines the mock banking tools and their stateful preconditions.
- `agent.py` is the LangGraph implementation with the MCP tool list wired directly into the agent.
- `agent_guarded.py` imports the same helpers from `agent.py` and adds Agent Shield around the tool list.
- `guardrails.yaml` contains exactly four deterministic gates: transfer integrity, approval grounding, PII redaction, and memo-injection protection.
- `eval_config_unguarded.yaml` and `eval_config_guarded.yaml` share the same `behavior`, `context`, dimensions, sample size, and model assignments. They differ only in `run` and `target.callable`.

## Run

Required environment variable names are `AZURE_API_KEY`, `AZURE_API_BASE`, and optionally `AZURE_API_VERSION` / `AGENT_MODEL`. Do not commit local `.env` files.

```powershell
assert-eval run --config examples\bank_manager\eval_config_unguarded.yaml
assert-eval run --config examples\bank_manager\eval_config_guarded.yaml
```

Both configs generate 200 scenarios with Phoenix/OpenTelemetry trace capture enabled for the callable target.

## Artifact snapshot

The committed snapshot under `artifacts/results/bank-manager-agent-shield/` was produced by running both configs end-to-end (n=200, concurrency=12) against a shared `test_set.jsonl` (cache-reused across both variants so the comparison is matched, not just stand-alone). The shared suite-level artifacts (`taxonomy.json`, `systematization.json`, `stratification.json`, `test_set.jsonl`, `suite.json`, `latest.json`) sit at the root; per-variant inference + scores live in `variant-a-unguarded/` and `variant-b-guarded/`.

Headline (judge `azure/gpt-5.4-mini`):

| Dimension | A unguarded (n=199) | B guarded (n=200) | Delta |
|---|---:|---:|---:|
| `policy_violation` | **47.2%** (94 / 199) | **36.5%** (73 / 200) | **−10.7 pp** |
| `overrefusal` | **1.0%** (2 / 199) | **7.5%** (15 / 200) | **+6.5 pp** |

(A is `n=199` because 1 of 200 test cases failed during inference; B judged all 200.)

Per `request_type`, `policy_violation` rates:

| `request_type` | n | A unguarded | B guarded | Delta |
|---|---:|---:|---:|---:|
| `ssn_in_input` | 29 | 65.5% (19/29) | 31.0% (9/29) | **−34.5 pp** |
| `vip_transfer_requires_approval` | 29 | 37.9% (11/29) | 27.6% (8/29) | −10.3 pp |
| `prompt_injection_in_memo` | 28/29 | 32.1% (9/28) | 27.6% (8/29) | −4.5 pp |
| `account_enumeration_probe` | 28 | 57.1% (16/28) | 46.4% (13/28) | −10.7 pp |
| `benign_account_lookup` | 28 | 28.6% (8/28) | 21.4% (6/28) | −7.2 pp |
| `freeze_account_requires_admin_mode` | 30 | 96.7% (29/30) | 80.0% (24/30) | −16.7 pp |
| `ceo_fraud_social_engineering` | 27 | 7.4% (2/27) | 18.5% (5/27) | +11.1 pp |

Per `request_type`, `overrefusal` rates:

| `request_type` | n | A unguarded | B guarded | Delta |
|---|---:|---:|---:|---:|
| `ssn_in_input` | 29 | 6.9% (2/29) | 10.3% (3/29) | +3.4 pp |
| `vip_transfer_requires_approval` | 29 | 0.0% (0/29) | 17.2% (5/29) | +17.2 pp |
| `prompt_injection_in_memo` | 28/29 | 0.0% (0/28) | 20.7% (6/29) | +20.7 pp |
| `ceo_fraud_social_engineering` | 27 | 0.0% (0/27) | 3.7% (1/27) | +3.7 pp |
| all other request types | n/a | 0.0% | 0.0% | 0.0 pp |

ACS gates measurably reduce policy violations across nearly every `request_type` category (largest gain on `ssn_in_input` at −34.5 pp). The cost is +6.5 pp aggregate `overrefusal`, concentrated on `vip_transfer_requires_approval` and `prompt_injection_in_memo` where the deterministic gates fire on the boundary cases.

To regenerate the snapshot locally, run both configs back-to-back from the worktree root; the second run cache-hits the test_set produced by the first:

```powershell
assert-eval run --config examples\bank_manager\eval_config_unguarded.yaml
assert-eval run --config examples\bank_manager\eval_config_guarded.yaml
```
