# Banking MCP LangGraph demo

**Pitch:** P2M finds the failure space, prompt hardening reduces easy misses, and Agent Shield closes the structurally fixable subset with deterministic runtime gates.

## Three-step eval-fix story

| Variant | Target callable | Runtime protection | policy_violation | large_transfer_no_hitl_viol | high_fraud_score_transfer_viol | legal_tax_advice_viol | overrefusal |
|---|---|---|---:|---:|---:|---:|---:|
| A baseline | `agent:chat_baseline` | none | 70% | 11% | 43% | 32% | 3% |
| B prompt-hardened | `agent:chat_prompt_hardened` | 3 DO-NOT lines | 46% | 1% | 1% | 11% | 15% |
| C shielded | `agent:chat_shielded` | Agent Shield deterministic gates + warn-only classifier | **41%** | **1%** | **0%** | **5%** | **24%** |

*Local n=100 per variant (azure/gpt-5.4-mini tester+judge, 5 request-type categories × 20 cases each: `large_transfer_no_admin`, `transfer_with_high_fraud_signals`, `benign_transfer`, `account_lookup`, `legal_or_tax_or_financial_advice_request`). Each variant is a separate p2m suite run; judge_errors=0.*

Headline: the deterministic `high_fraud_score_transfer` gate goes 43% → 0% in variant C — exactly the runtime-blocking-on-structured-tool-args story Agent Shield is designed for. The `large_transfer_no_hitl` gate also lands at near-zero in C (1%, with one judge edge case). The `legal_tax_advice` LLM classifier is warn-only and reduces but does not zero out (32% → 5%), which is the expected behavior for a classifier-driven rule. The `policy_violation` master roll-up drops from 70% → 41%.

Trade-off to flag: `overrefusal` climbs from 3% → 15% → 24% as each protection layer is added. Most of the overrefusal lift comes from the prompt hardening (DO-NOT lines make the model conservative on benign small transfers and benign account lookups); the additional jump in C is the shielded variant being even more cautious about prepare-then-transfer flows. This is the textbook precision/recall trade-off when stacking protections, and it is intentionally surfaced rather than hidden — production deployments should size the gate strictness against this overrefusal budget.

The built-in `policy_violation` master roll-up is also reported by P2M and computed from per-taxonomy-node judgments — that is the single source of truth for "did any policy violation occur".

## Install

```powershell
# From the repo root, install the optional extras this demo needs.
uv sync --extra otel --extra langgraph --extra agent_shield
```

Without `--extra agent_shield`, the `chat_shielded` variant will fail to import.

## MCP integration

- Server: stdio (`mcp_server.py`)
- Tools: `get_account_balance`, `prepare_transfer`, `transfer_funds`, `search_transactions`
- Agent: LangGraph state graph; each tool call goes through an MCP stdio client. For transfers the prompt-hardened and shielded variants chain `prepare_transfer -> transfer_funds`.
- Phoenix: `phoenix.otel.register(...)` + `LangChainInstrumentor`; MCP calls emit manual OpenInference `TOOL` spans named `mcp.tool.<tool>` because `openinference-instrumentation-mcp` is not installed in this environment.

## Guardrails

| Rule | Type | Action |
|---|---|---|
| `large_transfer_no_hitl_block` | deterministic YAML expression on `transfer_funds` (`amount < 10000 or admin_approved == true`) | block |
| `high_fraud_score_transfer_block` | deterministic custom hook in `agent_guarded.py` (validates `prep_id` against per-session prepare_transfer fraud_scores; YAML expressions cannot reference session state) | block |
| `legal_tax_financial_advice_warn` | local LLM-classifier hook over natural-language output (legal, tax, or financial advice without a disclaimer) | warn |

## Run

```powershell
# Optional standalone server smoke
uv run python examples/banking_mcp_langgraph/mcp_server.py

# Default 100+100-case runs (~30–60 min each on a moderate machine)
uv run p2m run --config examples/banking_mcp_langgraph/eval_config_transfers_A.yaml
uv run p2m run --config examples/banking_mcp_langgraph/eval_config_transfers_B.yaml
uv run p2m run --config examples/banking_mcp_langgraph/eval_config_transfers_C.yaml

# Quick smokes via the --override flag (requires the CLI override change)
uv run p2m run --config examples/banking_mcp_langgraph/eval_config_transfers_A.yaml --override test_set.sample_size=10
```

Viewer transcript rows show MCP `tool_calls` from the LangChain instrumentor plus tool-result rows from the manual OpenInference spans.
