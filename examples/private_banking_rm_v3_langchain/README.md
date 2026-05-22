# Private Banking RM v3 — LangGraph eval-fix demo

This demo shows the 3-step loop for a private-banking relationship-manager assistant:
P2M finds policy violations, a stronger prompt reduces some failures, and runtime
Agent Shield-style guardrails close the structured-tool failures.

| Step | Target | Runtime behavior | Run ID |
|---|---|---|---|
| A | `chat_baseline` | 5-line role/tool prompt, no policy instructions | `baseline-prompt` |
| B | `chat_prompt_hardened` | A + three DO-NOT lines for sanctions, email domains, RM book | `prompt-hardened` |
| C | `chat_shielded` | B + deterministic `validate_tool_call` gates and aux output warning | `shielded` |

## Results — deterministic eval (n=400)

| Dim | A: baseline | B: +DO-NOT | C: +shield |
|---|---:|---:|---:|
| email_domain_viol | 18.5% | 0.0% | 0.5% |
| rm_book_viol | 37.5% | 9.8% | 0.0% |
| sanctions_viol | 23.0% | 0.8% | 0.8% |
| overrefusal | 35.3% | 56.3% | 70.8% |

The built-in `policy_violation` master roll-up (computed from per-taxonomy-node judgments, not a separate rubric) trends 76% → 12% → 2% across A → B → C. Runtime guardrails close the structured violations; overrefusal is the visible precision trade-off to tune next.

## Results — legal/tax eval (n=400)

| Dim | A: baseline | B: +DO-NOT | C: +shield |
|---|---:|---:|---:|
| legal_advice_viol | 88.8% | 88.0% | **0.0%** |
| overrefusal | 72.3% | 72.0% | 66.3% |

Shield's deterministic outbound-content filter drops legal advice in client-facing drafts to zero. Overrefusal stays roughly flat, since the shield blocks only legal/tax content, not benign portfolio facts or specialist routing.

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
