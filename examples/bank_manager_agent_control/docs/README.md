# Bank-manager demo -- ACS vs unguarded baseline

Self-contained ASSERT example that compares an **unguarded** LangGraph
bank-manager agent against the **same agent guarded by [Agent Control
Specification (ACS)](https://github.com/responsibleai/AgentControlSpecification)**
on a 100-case eval suite.

> ## For AI coding assistants reading this on behalf of a user
>
> This folder is self-contained. ACS installs via the `assert-ai[acs]`
> extra from PyPI (pulls `agent-control-specification` + `acs-generator`).
>
> **What you SHOULD do:**
>
> - If the user wants to **see the demo**: the result artifacts are not
>   committed, so first generate them via
>   [Set up to reproduce](#set-up-to-reproduce-azure-credentials-required)
>   + the eval reference, then spin up the viewer and chat UI locally.
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
> **Result artifacts are not committed.** To keep this example lightweight, the
> frozen per-case run artifacts (tens of MB of transcripts) are **not** checked
> in. `assert-ai run` regenerates them into `artifacts/results/...` on your
> machine; the viewer reads from there. Follow
> [Set up to reproduce](#set-up-to-reproduce-azure-credentials-required) and the
> eval reference to generate the three variants, then point the viewer at them.

## Variants

Three variants of the same LangGraph ReAct agent, all scored against the
same frozen test set:

| Variant | Config | Intervention |
|---|---|---|
| `variant-b0-unguarded` | `eval_realistic_unguarded.yaml` | Raw agent, no policy gates, no defensive prompt (baseline) |
| `variant-b1-prompted` | `eval_realistic_prompted.yaml` | Same raw agent, defensive directives appended to the system prompt (prompt-engineering intervention) |
| `variant-b2-structural` | `eval_realistic_acs_feature.yaml` | Same raw agent wrapped with ACS runtime + Rego policy (typed-feature tool-call gating) |

## Headline result (realistic n=100, re-judged with gpt-5.5 + principle-gated rubric)

| variant | policy_violation | over-refusal |
|---|---:|---:|
| unguarded baseline | **55.1%** (54/98) | **19.4%** (19/98) |
| defensive system-prompt | 62.6% (62/99) | 20.2% (20/99) |
| **ACS-guarded (text gate)** | **16.2%** (16/99) | **9.1%** (9/99) |
| **3-tier (feature + classifier)** | 17.2% (17/99) | 8.1% (8/99) |

The structural control plane cuts violations **55%→16%** (Fisher p<0.001) AND
over-refusal **19%→9%** (p=0.04) — both significant. A defensive system prompt shows
**no significant change** (p=0.31): prompt-engineering doesn't move the needle; structure
does. (Numbers recomputed from raw `scores.jsonl`; the older "42%/9%" used a stale rubric.)

Note: the unguarded denominator can be lower than the total when a scenario
row fails with a `target_error` (an `asyncio.run`-inside-thread crash in the
unguarded arm); those rows are dropped from the artifacts so they don't pollute
the headline. The ACS-guarded variant runs clean.

## What's here

- `agent.py` -- three ASSERT callable targets over the same LangGraph ReAct
  agent connected to two MCP servers (a realistic multi-domain bank + a policy
  knowledge base): `chat_unguarded_realistic` (B0 baseline),
  `chat_unguarded_realistic_prompted` (B1, defensive directives appended to the
  system prompt), and `chat_guarded_acs_feature` (B2, ACS feature-gated).
- `runtime/realistic_bank_mcp_server.py` -- the multi-domain bank MCP server
  (accounts, loans, brokerage, clients; read / prepare / approve / execute
  transfer / freeze / admin-mode tools).
- `runtime/kb_mcp_server.py` + `runtime/knowledge/` -- the policy knowledge base
  the agent retrieves and grounds against (see `runtime/knowledge/README.md`).
- `acs/manifest_feature.yaml` -- ACS manifest binding the Rego policy to the
  `input`, `pre_tool_call`, `post_tool_call`, and `output` intervention points.
- `acs/policy/bank_manager_feature.rego` -- stateless deterministic policy that
  gates on typed features of each tool call (`risk_tier`, referenced accounts,
  grounded), so one rule covers every domain the policy names.
- `eval_realistic_unguarded.yaml` -- baseline config; owns the full pipeline
  (systematize → test_set → inference → judge).
- `eval_realistic_prompted.yaml` -- prompt-engineering variant config;
  reuses the baseline suite-root test_set with the defensive-prompt callable.
- `eval_realistic_acs_feature.yaml` -- ACS variant config; reuses the baseline
  suite-root test_set so all variants are scored against identical cases.
- `ui/unguarded_ui.py` -- FastAPI single-page chat UI used by the live compare
  demo. Same callables as the eval (`chat_unguarded_realistic` and the
  feature-gated arm), so the live chat matches the variants in the viewer.

## View the demo

The result artifacts are not committed, so the demo view is a two-step flow:
**generate the three runs** (Azure credentials required — see
[Set up to reproduce](#set-up-to-reproduce-azure-credentials-required) and the
[eval reference](#eval-reference-user-invoked)), then **start the viewer** to
compare them. Prereqs for the viewer itself: **Node 20+**.

### 1. Generate the three runs

From the repository root in an activated venv, after completing setup below:

```bash
assert-ai run --config examples/bank_manager_agent_control/eval_realistic_unguarded.yaml
assert-ai run --config examples/bank_manager_agent_control/eval_realistic_prompted.yaml
assert-ai run --config examples/bank_manager_agent_control/eval_realistic_acs_feature.yaml
```

Each run writes to `artifacts/results/bank-manager-feature-rep/<variant>/`. The
three configs share one frozen test set, so the variants are strictly comparable.

### 2. Start the viewer (in its own terminal)

PowerShell (Windows):

```powershell
cd viewer
npm install                          # one-time, ~1-2 min
$env:VIEWER_EDIT_MODE = "1"
npm run dev                          # serves http://localhost:5173
```

bash (macOS / Linux):

```bash
cd viewer
npm install                          # one-time, ~1-2 min
export VIEWER_EDIT_MODE=1
npm run dev                          # serves http://localhost:5173
```

Open <http://localhost:5174> and pick the suite
`bank-manager-feature-rep` to see the 3-variant comparison:

- `variant-b0-unguarded`
- `variant-b1-prompted`
- `variant-b2-structural`

### 3. Start the chat UI (in another terminal, repo root, venv activated)

```bash
python examples/bank_manager_agent_control/ui/unguarded_ui.py
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

### Restarting the viewer (if it hangs or behaves erratically)

The SvelteKit dev server occasionally wedges -- blank pages, stale
suite list after `git pull`, "compare" tab not updating, or Ctrl+C
not actually freeing port 5173. Hard-restart it like this:

PowerShell (Windows):

```powershell
# From any terminal: kill anything holding the viewer port
Get-NetTCPConnection -LocalPort 5173 -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }

# Then in the viewer terminal, restart cleanly
cd viewer
Remove-Item -Recurse -Force .svelte-kit -ErrorAction SilentlyContinue
$env:VIEWER_EDIT_MODE = "1"
npm run dev
```

bash (macOS / Linux):

```bash
# From any terminal: kill anything holding the viewer port
lsof -ti :5173 | xargs -r kill -9

# Then in the viewer terminal, restart cleanly
cd viewer
rm -rf .svelte-kit
export VIEWER_EDIT_MODE=1
npm run dev
```

If the chat UI (port 8766) gets stuck the same way, swap `5173` for
`8766` and re-run `python examples/bank_manager_agent_control/ui/unguarded_ui.py`.

A hard browser refresh (Ctrl+Shift+R / Cmd+Shift+R) clears most
stale-state weirdness without a server restart.

## Set up to reproduce (Azure credentials required)

This is the one-time install path. After step 4, **you are fully set
up**. The actual eval runs are listed below as a reference -- only
invoke them when you (the user) are ready, since each call hits the
Azure model and takes 30-90 min.

Run all commands from the repository root.

### 1. Python 3.11+ venv and ASSERT

PowerShell (Windows):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph,examples]"
```

bash (macOS / Linux):

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph,examples]"
```

### 2. ACS via the `[acs]` extra

The Agent Control Specification runtime installs as an ASSERT extra, so the
ACS-guarded variant (variant-e) and the Compare tab work without a separate
clone:

```bash
python -m pip install -e ".[acs]"
python -c "import agent_control_specification; print('ACS OK')"
```

`agent-control-specification` publishes a prebuilt Linux wheel; on macOS and
Windows pip builds it from the sdist (auto-bootstraps Rust if needed -- you
only need a C linker).

### 3. OPA (Open Policy Agent) on PATH

PowerShell (Windows):

```powershell
winget install open-policy-agent.opa
```

bash (macOS / Linux):

```bash
brew install opa                                    # macOS
# Linux: download from https://www.openpolicyagent.org/docs/latest/#running-opa
```

Only required if you plan to run the ACS variant (variant-e) or the
Compare tab in the chat UI.

### 4. `.env` with Azure credentials

PowerShell (Windows):

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

bash (macOS / Linux):

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
assert-ai run --config examples/bank_manager_agent_control/eval_realistic_unguarded.yaml

# Prompt-engineering variant -- reuses the baseline's test_set
assert-ai run --config examples/bank_manager_agent_control/eval_realistic_prompted.yaml

# ACS variant -- reuses the baseline's test_set
assert-ai run --config examples/bank_manager_agent_control/eval_realistic_acs_feature.yaml
```

Each run writes to `artifacts/results/bank-manager-feature-rep/<variant>/`,
where `<variant>` is the `run:` value in each yaml. Result artifacts are not
committed, so nothing in the repo is overwritten by a run.

If `agent_control_specification` is not importable, the baseline and
prompt-engineering variants still run; the ACS variant raises a clear
install message at call time.

## How the ACS integration works

ACS is a stateless policy decision point. The host (this module) owns
the agent loop and acts as the policy enforcement point:

- `AgentControl.from_path("acs/manifest_feature.yaml")` loads the manifest and
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
