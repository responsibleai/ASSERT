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

The committed snapshot under `artifacts/results/bank-manager-agent-shield/` was produced by running `eval_config_guarded.yaml` end-to-end (n=200, concurrency=12). It contains the shared suite-level artifacts (`taxonomy.json`, `systematization.json`, `stratification.json`, `test_set.jsonl`, `suite.json`, `latest.json`) plus the `variant-b-guarded/` response folder (`inference_set.jsonl`, `scores.jsonl`, `metrics.json`, and viewer indexes).

Headline numbers for `variant-b-guarded` (200 scored test cases, judge `azure/gpt-5.4-mini`):

| Dimension | Rate |
|---|---:|
| `policy_violation` | 27.0% (54 / 200) |
| `overrefusal` | 10.5% (21 / 200) |

`variant-a-unguarded/` response artifacts are intentionally not committed; `.gitignore` blocks that folder. To regenerate locally, run the unguarded config and inspect under `artifacts/results/bank-manager-agent-shield/variant-a-unguarded/`.
