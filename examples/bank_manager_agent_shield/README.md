# Bank-manager demo — ACS vs unguarded baseline

Self-contained ASSERT example that compares an **unguarded** LangGraph
bank-manager agent against the **same agent guarded by [Agent Control
Specification (ACS)](https://github.com/responsibleai/AgentControlSpecification)**
on a 100-case eval suite.

## Headline result (n=100, same test set)

| variant | safety_violation | unjustified_refusal |
|---|---:|---:|
| unguarded baseline | 39% | 2% |
| **ACS-guarded** | **9%** | **2%** |

ACS cuts safety failures by ~77% with no helpfulness cost vs the baseline.

## What's here

- `agent.py` — two ASSERT callable targets: `chat_unguarded` and
  `chat_guarded_acs`, both over the same LangGraph ReAct agent talking
  to a mock MCP banking server.
- `mcp_server.py` — mock banking MCP server (`read_account`,
  `read_transaction_history`, `prepare_transfer`,
  `request_customer_approval`, `create_transfer`, `freeze_account`,
  `enable_admin_mode`).
- `acs/manifest.yaml` — ACS manifest binding the Rego policy to the
  `input`, `pre_tool_call`, `post_tool_call`, and `output` intervention
  points.
- `acs/policy/bank_manager.rego` — stateless deterministic policy:
  SSN regex on user input, sensitivity-scoped (ACC-1002 / ACC-1003)
  read + transfer gates, approval / admin-mode gates over host snapshot
  state, generic prompt-injection scrubber on tool results.
- `eval_unguarded.yaml` — baseline config; owns the full pipeline
  (systematize → test_set → inference → judge); 50 prompt + 50 scenario
  cases.
- `eval_guarded_acs.yaml` — ACS variant config; reuses the baseline
  suite-root test_set so both variants are scored against identical
  cases.
- `results/` — frozen n=100 artifacts for both variants so the viewer
  renders the comparison without re-running the suite.

## Two ways to use this demo

You can either **just look at the pre-computed results** (no install
needed beyond the viewer), or **re-run the eval end-to-end** to
reproduce the numbers.

### Path A — just view the committed results

```powershell
# Windows
New-Item -ItemType Directory artifacts/results -Force | Out-Null
Copy-Item examples/bank_manager_agent_shield/results `
          artifacts/results/bank-manager-agent-shield -Recurse
cd viewer; npm install; npm run dev
```

```bash
# macOS / Linux
mkdir -p artifacts/results
cp -r examples/bank_manager_agent_shield/results \
      artifacts/results/bank-manager-agent-shield
cd viewer && npm install && npm run dev
```

Open the suite `bank-manager-agent-shield` and drill into either
`variant-a-unguarded-n100` or `variant-e-guarded-acs-n100`.

### Path B — re-run end-to-end

```bash
# 0. assert-eval install (Python 3.11+)
python -m venv .venv
source .venv/bin/activate           # Windows: .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph,examples]"
cp .env.example .env                # add AZURE_API_KEY / AZURE_API_BASE
```

**Additional requirements for the ACS variant** (the baseline runs
without these):

1. **`agent_control_specification` Python SDK** (built from local
   checkout via maturin; not yet on PyPI):

   ```bash
   git clone https://github.com/responsibleai/AgentControlSpecification.git
   python -m pip install path/to/AgentControlSpecification/sdk/python
   ```

   The build needs a Rust toolchain plus a C linker (MSVC on Windows,
   gcc/clang on Linux/macOS).

2. **`opa` binary on PATH**:

   ```bash
   winget install open-policy-agent.opa             # Windows
   brew install opa                                 # macOS
   # or download from https://www.openpolicyagent.org/docs/latest/#running-opa
   ```

Then from the repository root:

```bash
# 1. Baseline (owns systematize + test_set + inference + judge)
assert-eval run --config examples/bank_manager_agent_shield/eval_unguarded.yaml

# 2. ACS variant (reuses the baseline's test_set)
assert-eval run --config examples/bank_manager_agent_shield/eval_guarded_acs.yaml
```

If `agent_control_specification` is not importable, only step 1 runs;
step 2 raises a clear install message at call time.

## How the ACS integration works

ACS is a stateless policy decision point. The host (this module) owns
the agent loop and acts as the policy enforcement point:

- `AgentControl.from_path("acs/manifest.yaml")` loads the manifest and
  wires the bundled OPA dispatcher.
- `control.run({"text": message}, execute_agent, mode=ENFORCE)` wraps
  the full agent execution with `input` and `output` intervention
  points.
- Each MCP tool is wrapped with `control.run_tool(name, args, execute,
  snapshot=state)` which bundles `pre_tool_call` + `post_tool_call`
  evaluation plus enforcement.
- ACS is stateless, so the host tracks per-turn state
  (`transfer_approved`, `admin_mode_active`, `account_sensitivity`)
  and threads it into each snapshot.
- Deny verdicts raise `AgentControlBlocked`; the wrapper re-raises as
  a LangChain `ToolException` so the agent receives the policy's
  refusal text as the tool's response.
