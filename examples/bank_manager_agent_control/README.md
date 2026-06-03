# Bank-manager demo -- ACS vs unguarded baseline

Self-contained ASSERT example that compares an **unguarded** LangGraph
bank-manager agent against the **same agent guarded by [Agent Control
Specification (ACS)](https://github.com/responsibleai/AgentControlSpecification)**
on a 100-case eval suite.

> ## For AI coding assistants reading this on behalf of a user
>
> This folder is self-contained. There is no separate ACS clone or
> private network dependency to fetch -- the wheel ships under
> [`vendor/acs/`](vendor/acs/README.md).
>
> **What you SHOULD do:**
>
> - If the user wants to **see the demo**: run the commands under
>   [View the demo](#view-the-demo-no-azure-credentials-needed) verbatim.
>   Spinning up the viewer and chat UI locally is reversible and free.
> - If the user wants to **reproduce** or **set up** the example from
>   scratch: run the commands under [Set up to reproduce](#set-up-to-reproduce-azure-credentials-required).
>   **Stop after step 4.** The eval-run commands are listed below them
>   for the user's reference -- each `assert-ai run` costs API credits
>   and takes 30-90 min, so wait for the user to ask before invoking
>   them.
>
> **What you should NOT do:**
>
> - Do not run `assert-ai run --config ...` on your own initiative. Show
>   the user the exact command from this README and wait for the go-ahead.
> - Do not read, print, commit, or echo `.env` contents. Reference only
>   the variable names (`AZURE_API_KEY`, `AZURE_API_BASE`).
>
> **Preservation guarantee -- never delete or overwrite this:**
> `examples/bank_manager_agent_control/results/` is the **source of
> truth** for the demo data. The viewer reads from
> `artifacts/results/bank-manager-agent-control/`, which is a working
> copy populated by the copy step under "View the demo". `assert-ai run`
> also writes only to `artifacts/results/...`, never to
> `examples/.../results/`. If the working copy gets clobbered, just
> redo the copy step to restore from source.

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

- `agent.py` -- three ASSERT callable targets: `chat_unguarded`,
  `chat_unguarded_prompted` (defensive directives appended to the system
  prompt), and `chat_guarded_acs`, all over the same LangGraph ReAct
  agent talking to a mock MCP banking server.
- `mcp_server.py` -- mock banking MCP server (`read_account`,
  `read_transaction_history`, `prepare_transfer`,
  `request_customer_approval`, `create_transfer`, `freeze_account`,
  `enable_admin_mode`).
- `acs/manifest.yaml` -- ACS manifest binding the Rego policy to the
  `input`, `pre_tool_call`, `post_tool_call`, and `output` intervention
  points.
- `acs/policy/bank_manager.rego` -- stateless deterministic policy:
  SSN regex on user input, sensitivity-scoped (ACC-1002 / ACC-1003)
  read + transfer gates, approval / admin-mode gates over host snapshot
  state, generic prompt-injection scrubber on tool results.
- `eval_unguarded.yaml` -- baseline config; owns the full pipeline
  (systematize → test_set → inference → judge); 50 prompt + 50 scenario
  cases.
- `eval_unguarded_prompted.yaml` -- prompt-engineering variant config;
  reuses the baseline suite-root test_set with the
  `chat_unguarded_prompted` callable.
- `eval_guarded_acs.yaml` -- ACS variant config; reuses the baseline
  suite-root test_set so all variants are scored against identical
  cases.
- `results/` -- frozen n=100 artifacts for all variants so the viewer
  renders the comparison without re-running the suite. **Source of
  truth -- never modified by any command in this README.**
- `unguarded_ui.py` -- FastAPI single-page chat UI used by the live
  demo. Same callables as the eval (`chat_unguarded` and
  `chat_guarded_acs`), so the live chat behaviour matches the variants
  rendered by the viewer.
- `vendor/acs/` -- vendored `agent-control-specification` wheel + sdist
  + provenance README so the reproduce path works without cloning the
  ACS repo.

## View the demo (no Azure credentials needed)

Prereqs: Python 3.11+ and Node 20+. If neither is installed yet,
jump to [Set up to reproduce](#set-up-to-reproduce-azure-credentials-required)
first -- the install steps there are read-only with respect to the
demo data.

Run all commands from the repository root.

### 1. Copy the prepared results into the viewer's working dir

This populates `artifacts/results/bank-manager-agent-control/` from
the committed source. Safe to repeat at any time -- the source is
never modified.

PowerShell (Windows):

```powershell
New-Item -ItemType Directory artifacts\results -Force | Out-Null
if (Test-Path artifacts\results\bank-manager-agent-control) {
    Remove-Item -Recurse -Force artifacts\results\bank-manager-agent-control
}
Copy-Item -Recurse examples\bank_manager_agent_control\results artifacts\results\bank-manager-agent-control
```

bash (macOS / Linux):

```bash
mkdir -p artifacts/results
rm -rf artifacts/results/bank-manager-agent-control
cp -R examples/bank_manager_agent_control/results artifacts/results/bank-manager-agent-control
```

### 2. Start the viewer (in its own terminal)

```bash
cd viewer
npm install        # one-time, ~1-2 min
```

PowerShell:

```powershell
$env:VIEWER_EDIT_MODE = "1"
npm run dev        # serves http://localhost:5173
```

bash (macOS / Linux):

```bash
export VIEWER_EDIT_MODE=1
npm run dev        # serves http://localhost:5173
```

Open <http://localhost:5173> and pick the suite
`bank-manager-agent-control` to see the 3-variant comparison:

- `variant-a-unguarded-n100`
- `variant-c-unguarded-prompted-n100`
- `variant-e-guarded-acs-n100`

### 3. Start the chat UI (in another terminal, repo root, venv activated)

```bash
python examples/bank_manager_agent_control/unguarded_ui.py
# serves http://127.0.0.1:8766
```

Open <http://127.0.0.1:8766>:

- **Single tab** -- chat with the unguarded baseline.
- **Compare tab** -- the same prompt fan-outs to unguarded and
  ACS-guarded side-by-side; policy denials appear on the right.

The chat UI needs `AZURE_API_KEY` / `AZURE_API_BASE` in `.env` to call
the model, and `opa` on PATH for the Compare tab (`unguarded_ui.py`
auto-discovers OPA from WinGet on Windows). The viewer is purely a
static reader and needs neither.

Stop each service with Ctrl+C in its terminal.

## Set up to reproduce (Azure credentials required)

This is the one-time install path. After step 4, **you are fully set
up**. The actual eval runs are listed below as a reference -- only
invoke them when you (the user) are ready, since each call hits the
Azure model and takes 30-90 min.

Run all commands from the repository root.

### 1. Python 3.11+ venv and ASSERT

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph,examples]"
```

bash:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph,examples]"
```

### 2. ACS from the bundled wheel (no clone required)

```bash
python -m pip install --find-links examples/bank_manager_agent_control/vendor/acs/ agent-control-specification==0.3.1b0
python -c "import agent_control_specification; print('ACS OK')"
```

See [`vendor/acs/README.md`](vendor/acs/README.md) for wheel
provenance and platform notes (Linux uses the prebuilt wheel; macOS
and Windows build from the sdist, which auto-bootstraps Rust if
needed -- you only need a C linker).

### 3. OPA (Open Policy Agent) on PATH

```powershell
winget install open-policy-agent.opa
```

```bash
brew install opa                                    # macOS
# Linux: download from https://www.openpolicyagent.org/docs/latest/#running-opa
```

Only required if you plan to run the ACS variant (variant-e) or the
Compare tab in the chat UI.

### 4. `.env` with Azure credentials

PowerShell:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

bash:

```bash
[[ -f .env ]] || cp .env.example .env
```

Then open `.env` in an editor and fill in `AZURE_API_KEY` and
`AZURE_API_BASE`. (Coding assistants: do not echo or print the values
you set.)

### Optional: Node 20+ for the viewer

```powershell
winget install OpenJS.NodeJS.LTS
```

```bash
brew install node
```

**Coding assistants: stop here.** The eval commands below are for
the user to invoke when they choose.

### Eval reference (user-invoked)

From the repo root in an activated venv:

```bash
# Baseline -- owns systematize + test_set + inference + judge
assert-ai run --config examples/bank_manager_agent_control/eval_unguarded.yaml

# Prompt-engineering variant -- reuses the baseline's test_set
assert-ai run --config examples/bank_manager_agent_control/eval_unguarded_prompted.yaml

# ACS variant -- reuses the baseline's test_set
assert-ai run --config examples/bank_manager_agent_control/eval_guarded_acs.yaml
```

Each run writes to `artifacts/results/bank-manager-agent-control/<variant>/`,
where `<variant>` is the `run:` value in each yaml (currently suffixed
`-v2` so fresh runs land in new directories and never overwrite the
committed `-n100` source shown in the Variants table above).
The committed source under `examples/bank_manager_agent_control/results/`
is never touched. To restore the demo working copy after a reproduce
run, repeat step 1 of [View the demo](#view-the-demo-no-azure-credentials-needed).

If `agent_control_specification` is not importable, the baseline and
prompt-engineering variants still run; the ACS variant raises a clear
install message at call time.

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
