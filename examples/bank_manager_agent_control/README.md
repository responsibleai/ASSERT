# Bank-manager demo — ACS vs unguarded baseline

Self-contained ASSERT example that compares an **unguarded** LangGraph
bank-manager agent against the **same agent guarded by [Agent Control
Specification (ACS)](https://github.com/responsibleai/AgentControlSpecification)**
on a 100-case eval suite.

## Variants

Three variants of the same LangGraph ReAct agent, all scored against the
same frozen test set:

| Variant | Intervention |
|---|---|
| `variant-a-unguarded-n100` | Raw agent, no policy gates, no defensive prompt (baseline) |
| `variant-c-unguarded-prompted-n100` | Same raw agent, defensive directives appended to the system prompt (prompt-engineering intervention) |
| `variant-e-guarded-acs-n100` | Same raw agent wrapped with ACS runtime + Rego policy (tool-call gating) |

## Headline result (same test set)

| variant | safety_violation | unjustified_refusal |
|---|---:|---:|
| unguarded baseline (n=96) | 42% | 4% |
| **ACS-guarded (n=100)** | **9%** | **2%** |

ACS cuts safety failures by ~79% with no helpfulness cost vs the baseline.
See PR #197 for the full 3-way comparison including the prompt-engineering
variant.

Note: the unguarded denominator is 96 because 4 scenario rows
(`test_case_000053`, `_000063`, `_000065`, `_000090`) failed with a
`target_error` from an `asyncio.run`-inside-thread crash in
`agent.py:chat_unguarded` and were dropped from the artifacts so they
don't pollute the headline. The ACS-guarded variant ran clean at n=100.

## What's here

- `agent.py` — three ASSERT callable targets: `chat_unguarded`,
  `chat_unguarded_prompted` (defensive directives appended to the system
  prompt), and `chat_guarded_acs`, all over the same LangGraph ReAct
  agent talking to a mock MCP banking server.
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
- `eval_unguarded_prompted.yaml` — prompt-engineering variant config;
  reuses the baseline suite-root test_set with the
  `chat_unguarded_prompted` callable.
- `eval_guarded_acs.yaml` — ACS variant config; reuses the baseline
  suite-root test_set so all variants are scored against identical
  cases.
- `results/` — frozen n=100 artifacts for all variants so the viewer
  renders the comparison without re-running the suite.

## Two ways to use this demo

You can either **just look at the pre-computed results** (no install
needed beyond the viewer), or **re-run the eval end-to-end** to
reproduce the numbers.

### Path A — just view the committed results

```powershell
# Windows
New-Item -ItemType Directory artifacts/results -Force | Out-Null
Copy-Item examples/bank_manager_agent_control/results `
          artifacts/results/bank-manager-agent-control -Recurse
cd viewer; npm install; npm run dev
```

```bash
# macOS / Linux
mkdir -p artifacts/results
cp -r examples/bank_manager_agent_control/results \
      artifacts/results/bank-manager-agent-control
cd viewer && npm install && npm run dev
```

Open the suite `bank-manager-agent-control` and drill into
`variant-a-unguarded-n100`, `variant-c-unguarded-prompted-n100`, or
`variant-e-guarded-acs-n100`.

### Path B — re-run end-to-end

```bash
# 0. assert-ai install (Python 3.11+)
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
assert-ai run --config examples/bank_manager_agent_control/eval_unguarded.yaml

# 2. Prompt-engineering variant (reuses the baseline's test_set)
assert-ai run --config examples/bank_manager_agent_control/eval_unguarded_prompted.yaml

# 3. ACS variant (reuses the baseline's test_set)
assert-ai run --config examples/bank_manager_agent_control/eval_guarded_acs.yaml
```

If `agent_control_specification` is not importable, steps 1 and 2 still
run; step 3 raises a clear install message at call time.

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
