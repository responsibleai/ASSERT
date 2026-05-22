# Banking MCP LangGraph demo

**Pitch:** P2M finds the failure space, prompt hardening reduces easy misses, and Agent Shield closes the structurally fixable subset with deterministic runtime gates.

## Three-step eval-fix story (validated at n=400)

| Variant | Target callable | Runtime protection | sanctions_viol | fin_advice_viol | overrefusal |
|---|---|---|---:|---:|---:|
| A baseline | `agent:chat_baseline` | none | 78/400 (19.5%) | 49/400 (12.3%) | 90/400 (22.5%) |
| B prompt-hardened | `agent:chat_prompt_hardened` | 3 DO-NOT lines | 4/400 (1.0%) | 24/400 (6.0%) | 55/400 (13.8%) |
| C shielded | `agent:chat_shielded` | Agent Shield deterministic gate + warn-only classifier | **0/400 (0.0%)** | 12/400 (3.0%) | 88/400 (22.0%) |

Headline: the deterministic sanctions gate in C eliminates sanctioned-transfer execution entirely (0/400). The prompt-hardened B variant catches explicit sanctioned-country names but still misses some city/circumvention phrasing. The shielded C variant blocks on structured MCP tool parameters, so sanctioned transfer execution drops to zero.

The built-in `policy_violation` master roll-up is also reported by P2M and computed from per-taxonomy-node judgments — that is the single source of truth for "did any policy violation occur".

## Install

```powershell
# From the repo root, install the optional extras this demo needs.
uv sync --extra otel --extra langgraph --extra agent_shield
```

Without `--extra agent_shield`, the `chat_shielded` variant will fail to import.

## MCP integration

- Server: stdio (`mcp_server.py`)
- Tools: `get_account_balance`, `transfer_funds`, `search_transactions`
- Agent: LangGraph state graph; each tool call goes through an MCP stdio client.
- Phoenix: `phoenix.otel.register(...)` + `LangChainInstrumentor`; MCP calls emit manual OpenInference `TOOL` spans named `mcp.tool.<tool>` because `openinference-instrumentation-mcp` is not installed in this environment.

## Guardrails

| Rule | Type | Action |
|---|---|---|
| `sanctioned_country_transfer_block` | deterministic `@tool.params.destination_country` gate on `transfer_funds` | block |
| `financial_advice_disguised_as_fact` | local LLM-classifier hook over natural-language output | warn |

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

