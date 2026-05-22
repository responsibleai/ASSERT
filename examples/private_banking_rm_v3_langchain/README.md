# Private Banking RM v3 — LangGraph eval-fix demo

This demo shows the 3-step loop for a private-banking relationship-manager assistant:
P2M finds policy violations, a stronger prompt reduces some failures, and runtime
Agent Shield-style guardrails close the structured-tool failures.

| Step | Target | Runtime behavior | Run ID |
|---|---|---|---|
| A | `chat_baseline` | 5-line role/tool prompt, no policy instructions | `baseline-prompt` |
| B | `chat_prompt_hardened` | A + three DO-NOT lines for sanctions, email domains, RM book | `prompt-hardened` |
| C | `chat_shielded` | B + deterministic `validate_tool_call` gates and aux output warning | `shielded` |

## Results — deterministic eval

| Dim | A: baseline | B: +DO-NOT | C: +shield |
|---|---:|---:|---:|
| email_domain_viol | 16.5% | 0.0% | 0.0% |
| rm_book_viol | 31.5% | 6.5% | 0.0% |
| sanctions_viol | 20.5% | 0.0% | 0.5% |
| policy_viol | 61.5% | 6.5% | 0.5% |
| overrefusal | 38.5% | 51.5% | 63.5% |

Runtime guardrails close the structured violations; overrefusal is the visible precision trade-off to tune next.

## Results — legal/tax eval

| Dim | A: baseline | B: +DO-NOT | C: +shield |
|---|---:|---:|---:|
| legal_advice_viol | 83.0% | 79.0% | 0.0% |
| policy_viol | 94.0% | 83.0% | 92.0% |
| overrefusal | 60.0% | 61.0% | 53.0% |

## Run

```powershell
python -m pip install -e ".[otel,langgraph]"
$env:AZURE_API_VERSION = "2024-12-01-preview"
# Requires AZURE_API_KEY and AZURE_API_BASE in the shell environment.

uv run p2m run --config examples/private_banking_rm_v3_langchain/eval_config_deterministic_A.yaml
uv run p2m run --config examples/private_banking_rm_v3_langchain/eval_config_deterministic_B.yaml
uv run p2m run --config examples/private_banking_rm_v3_langchain/eval_config_deterministic_C.yaml
uv run p2m run --config examples/private_banking_rm_v3_langchain/eval_config_legal_tax_A.yaml
uv run p2m run --config examples/private_banking_rm_v3_langchain/eval_config_legal_tax_B.yaml
uv run p2m run --config examples/private_banking_rm_v3_langchain/eval_config_legal_tax_C.yaml
```

## Phoenix tracing

The agent imports `phoenix_setup.py`, which configures Phoenix/OpenInference for
LangChain/LangGraph and always writes a local JSONL span file at
`artifacts/phoenix/spans.jsonl`. To view traces live:

```powershell
$env:PHOENIX_COLLECTOR_ENDPOINT = "http://localhost:6006/v1/traces"
phoenix serve
```

Open the P2M viewer and select the audit tab for any run. Tool calls and tool
results should appear inline in the transcript pane.
