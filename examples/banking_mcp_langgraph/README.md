# Banking MCP LangGraph demo

**Pitch:** P2M finds the failure space, prompt hardening reduces easy misses, and Agent Shield closes the structurally fixable subset with deterministic runtime gates.

## Three-step eval-fix story

| Variant | Target callable | Runtime protection | large_transfer_no_hitl_viol | high_fraud_score_transfer_viol | legal_tax_advice_viol | overrefusal |
|---|---|---|---:|---:|---:|---:|
| A baseline | `agent:chat_baseline` | none | TBD (local n=400) | TBD (local n=400) | TBD (local n=400) | TBD (local n=400) |
| B prompt-hardened | `agent:chat_prompt_hardened` | 3 DO-NOT lines | TBD (local n=400) | TBD (local n=400) | TBD (local n=400) | TBD (local n=400) |
| C shielded | `agent:chat_shielded` | Agent Shield deterministic gates + warn-only classifier | **TBD (local n=400)** | **TBD (local n=400)** | TBD (local n=400) | TBD (local n=400) |

Headline: the two deterministic gates (`large_transfer_no_hitl` and `high_fraud_score_transfer`) are designed to drive their violation rates to **0%** in variant C via runtime blocking on structured MCP tool parameters. The `legal_tax_advice_viol` classifier remains warn-only and is expected to **reduce but not zero out** in variant C; the prompt hardening in B already lowers it but cannot guarantee a disclaimer on every advice turn. `overrefusal` is expected to stay flat or low across all three variants — the runtime gates only block on structured signals, not benign requests.

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
