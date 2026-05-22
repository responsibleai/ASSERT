# Banking MCP LangGraph demo

**Pitch:** P2M finds the failure space, prompt hardening reduces easy misses, and Agent Shield closes the structurally fixable subset with deterministic runtime gates.

## Three-step eval-fix story

| Variant | Target callable | Runtime protection | sanctions_viol | policy_viol | fin_advice_viol | overrefusal |
|---|---|---|---:|---:|---:|---:|
| A baseline | `agent:chat_baseline` | none | 25/100 | 34/100 | 1/100 | 3/100 |
| B prompt-hardened | `agent:chat_prompt_hardened` | 3 DO-NOT lines | 16/100 | 24/100 | 0/100 | 2/100 |
| C shielded | `agent:chat_shielded` | Agent Shield deterministic gate + warn-only classifier | 0/100 | 10/100 | 0/100 | 6/100 |

The prompt-hardened variant catches explicit sanctioned-country names, but still misses some city/circumvention phrasing. The shielded variant blocks on structured MCP tool parameters, so sanctioned transfer execution drops to zero in this run.

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

# Smokes
uv run p2m run --config examples/banking_mcp_langgraph/eval_config_transfers_A.yaml --override test_set.sample_size=10
uv run p2m run --config examples/banking_mcp_langgraph/eval_config_transfers_B.yaml --override test_set.sample_size=10
uv run p2m run --config examples/banking_mcp_langgraph/eval_config_transfers_C.yaml --override test_set.sample_size=10

# Full 100-case runs
uv run p2m run --config examples/banking_mcp_langgraph/eval_config_transfers_A.yaml
uv run p2m run --config examples/banking_mcp_langgraph/eval_config_transfers_B.yaml
uv run p2m run --config examples/banking_mcp_langgraph/eval_config_transfers_C.yaml
```

Viewer transcript rows should show `tool_calls` for MCP calls plus tool-result rows from the manual OpenInference spans.
