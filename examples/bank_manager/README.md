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

Both configs generate 100 scenarios with Phoenix/OpenTelemetry trace capture enabled for the callable target.

## Artifact snapshot policy

This repository does not include regenerated bank-manager result snapshots for the n=100 configs because a full inference and judge run spends model budget. The current CLI does not expose a pre-inference-only stage flag for regenerating only `taxonomy.json`, `systematization.json`, `stratification.json`, `test_set.jsonl`, `suite.json`, and `latest.json`.

If you intentionally produce a checked-in snapshot, run the guarded config and add only:

- `examples\bank_manager\artifacts\results\bank-manager-agent-shield\taxonomy.json`
- `examples\bank_manager\artifacts\results\bank-manager-agent-shield\systematization.json`
- `examples\bank_manager\artifacts\results\bank-manager-agent-shield\stratification.json`
- `examples\bank_manager\artifacts\results\bank-manager-agent-shield\test_set.jsonl`
- `examples\bank_manager\artifacts\results\bank-manager-agent-shield\suite.json`
- `examples\bank_manager\artifacts\results\bank-manager-agent-shield\latest.json`
- `examples\bank_manager\artifacts\results\bank-manager-agent-shield\variant-b-guarded\*`

Do not add `variant-a-unguarded\*`; `.gitignore` blocks that folder so unguarded responses are not accidentally committed.
