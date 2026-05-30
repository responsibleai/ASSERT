# Bank Manager AgentShield demo

Self-contained ASSERT example that wraps a LangGraph bank-manager agent
with [AgentShield](https://github.com/microsoft/AgentShield) guardrails
and scores three variants against the same 100-case eval suite.

## What's here

- `agent.py` — three ASSERT callable targets (`chat_unguarded`,
  `chat_guarded_v2`, `chat_guarded_v3`) over the same LangGraph ReAct
  agent.
- `mcp_server.py` — mock banking MCP server (`read_account`,
  `read_transaction_history`, `prepare_transfer`,
  `request_customer_approval`, `create_transfer`, `freeze_account`,
  `enable_admin_mode`).
- `guardrails.v2.yaml` — Stage 2 state-machine gates + one Stage 1
  SSN regex.
- `guardrails.v3.yaml` — sensitivity-scoped Stage 3 pattern gates +
  Stage 4 prompt-injection scrubber on tool results.
- `eval_unguarded_v2.yaml` — baseline config that owns the full
  pipeline (systematize → test_set → inference → judge); 50 prompt
  + 50 scenario cases.
- `eval_guarded_v2.yaml`, `eval_guarded_v3.yaml` — comparison configs
  that reuse the suite-root test_set produced by the baseline.
- `results/` — frozen n=100 artifacts for the baseline and v3
  variants so the viewer renders them without re-running the suite.

## Install

```bash
python -m venv .venv
source .venv/bin/activate           # Windows: .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph,examples]"
python -m pip install agent_shield  # required by the guarded variants
cp .env.example .env                # add AZURE_API_KEY / AZURE_API_BASE
```

The guarded variants import `agent_shield`, which is not pulled in by
the `examples` extra; install it separately as shown above.

## Run

From the repository root:

```bash
# 1. Baseline (owns systematize + test_set + inference + judge)
assert-eval run --config examples/bank_manager_agent_shield/eval_unguarded_v2.yaml

# 2. v2 comparison (reuses the baseline's test_set)
assert-eval run --config examples/bank_manager_agent_shield/eval_guarded_v2.yaml

# 3. v3 comparison
assert-eval run --config examples/bank_manager_agent_shield/eval_guarded_v3.yaml
```

Iterating on a guardrail YAML alone re-runs only the affected variant.
Changing the dimensions or `sample_size` in the baseline regenerates
the shared test_set and forces the comparison runs to re-score.

## View results

The viewer reads from `artifacts/results/` (gitignored), so seed it once
with the committed snapshot from this folder:

```bash
# macOS / Linux
mkdir -p artifacts/results
cp -r examples/bank_manager_agent_shield/results \
      artifacts/results/bank-manager-agent-shield
```

```powershell
# Windows
New-Item -ItemType Directory artifacts/results -Force | Out-Null
Copy-Item examples/bank_manager_agent_shield/results `
          artifacts/results/bank-manager-agent-shield -Recurse
```

Then start the viewer:

```bash
cd viewer
npm install
npm run dev
```

Open the suite `bank-manager-agent-shield` and drill into either
`variant-a-unguarded-n100` or `variant-d-guarded-v3-n100`. Per-run
pages show the custom judge dimensions `safety_violation` and
`unjustified_refusal` next to the built-in `policy_violation` /
`overrefusal`.

## Headline n=100 results

| run | safety_violation | unjustified_refusal |
|---|---:|---:|
| unguarded | 39% | 2% |
| v3 | **1%** | **2%** |

v3 cuts safety failures by 97% (39 → 1) with no helpfulness cost
versus the unguarded baseline.
