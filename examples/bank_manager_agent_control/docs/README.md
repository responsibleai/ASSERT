# Bank-manager demo — setup, reproduce, and how the ACS integration works

Self-contained ASSERT example comparing a **realistic baseline agent**, a **hardened
prompt**, and an **[ACS](https://github.com/responsibleai/AgentControlSpecification)
control plane** across two behaviors. Start with
[the example README](../README.md) for the behaviors, the arms, and the results;
this file is the setup and mechanics reference.

> ## For AI coding assistants reading this on behalf of a user
>
> This folder is self-contained. ACS installs via the `assert-ai[acs]` extra from PyPI
> (pulls `agent-control-specification` + `acs-generator`).
>
> **What you SHOULD do:**
>
> - If the user wants to **see the demo**: the result artifacts are not committed, so
>   first generate them via [Set up to reproduce](#set-up-to-reproduce-azure-credentials-required)
>   plus the eval reference, then spin up the viewer locally.
> - If the user wants to **reproduce** or **set up** the example from scratch: run the
>   commands under [Set up to reproduce](#set-up-to-reproduce-azure-credentials-required).
>   **Stop after step 4.** The eval-run commands are listed below them for the user's
>   reference — each `assert-ai run` costs API credits and takes 30–90 min, so wait for
>   the user to ask before invoking them.
>
> **What you should NOT do:**
>
> - Do not run `assert-ai run --config ...` on your own initiative. Show the user the exact
>   command from this README and wait for the go-ahead.
> - Do not read, print, commit, or echo `.env` contents. Reference only the variable names
>   (`AZURE_API_KEY`, `AZURE_API_BASE`).
>
> **Result artifacts are not committed.** To keep this example lightweight, the frozen
> per-case run artifacts (tens of MB of transcripts) are **not** checked in. `assert-ai run`
> regenerates them into `artifacts/results/...` on your machine; the viewer reads from there.

## The two behaviors at a glance

| | Behavior 1 · sensitivity-tier authorization | Behavior 2 · coercion via unverified authority |
|---|---|---|
| Suite | `tier-authorization` | `bank-manager-coercion-authority` |
| Configs | `eval_tier_authorization.yaml` (callable target), `eval_tier_authorization_traced.yaml` (connector target) | `eval_coercion_authority.yaml`, `eval_coercion_arm2_hardened.yaml`, `eval_coercion_arm3_acs.yaml`, `eval_coercion_arm3n_naive.yaml` |
| Arms | 1 realistic baseline · 2 defensive prompt · 3 ACS Rego | 1 realistic baseline · 2 hardened prompt · 3 ACS classifier annotator · 3n naive-scorer diagnostic |
| Cases per arm | 72 | 40 (19 coercive / 21 legitimate) |
| Control artifact | `acs/policy_tier_authz/tier_authorization.rego` | `acs/policy/bank_manager_coercion.rego` + `runtime/coercion_classifier.py` |
| Baseline artifact | `runtime/deposit_tier_gate.py` | `coercion_agent.py::BASE_PROMPT` + the keyword tripwire |

Headline numbers and every caveat live in [the example README](../README.md). Two that
matter most when quoting results:

- **Behavior 1's judge needs the tool sequence.** With `target.callable` (final assistant
  text only) `policy_violation` saturates at 62.5% across all three arms and produces false
  positives against a clean arm — the behavior is an *ordering* property. Use
  `eval_tier_authorization_traced.yaml` (`target.connector`) for judged numbers; the
  deterministic enforcement telemetry is the ground truth either way.
- **Behavior 2 is underpowered.** At n=19/21 no bypass delta reaches p<0.05 (all p=0.31);
  the closest contrast is Arm 2's over-refusal regression at p=0.061. Do not present the
  bypass deltas as statistically significant.

## What's here

- `bank_agent_common.py` — shared plumbing only: LLM construction (`AGENT_MODEL` routing,
  key or Entra auth), the bank + KB MCP server startup, final-text extraction, and the
  absolute-`bundle:` ACS manifest workaround. It deliberately declares **no** system prompt
  and **no** ASSERT callable — a shared module that also shipped a baseline agent is how the
  previous version of this demo ended up with a baseline nobody would defend in review.
- `agent_tier_authz.py` / `agent_tier_authz_adapter.py` — Behavior 1's three arms plus the
  connector adapter that serializes `tool_call` / `tool_result` events into the judge
  transcript.
- `coercion_agent.py` — Behavior 2's four arms, both prompts, the keyword tripwire, the ACS
  annotator runner, and the gate audit log.
- `runtime/tier_authz_mcp_server.py` — Behavior 1's MCP server: 11 tools across four domains
  plus `verify_authorization`, over the shared `bank_core` data model.
- `runtime/realistic_bank_mcp_server.py` + `runtime/bank_core.py` — the multi-domain bank
  (accounts, loans, brokerage, clients) used by Behavior 2 and by the offline tests.
- `runtime/kb_mcp_server.py` + `runtime/knowledge/` — the policy knowledge base the agent
  retrieves and grounds against (see `runtime/knowledge/README.md`).
- `runtime/feature_policy.py` — the typed-feature host state machine. Behavior 2 uses its
  snapshot builders to feed the ACS intervention points; `acs/policy/bank_manager_feature.rego`
  is the matching deterministic typed-signal policy, also used by the KB UI's grounding gate.
- `acs/manifest_tier_authorization.yaml`, `acs/manifest_coercion.yaml` — the two ACS manifests.
- `scripts/` — `generalization_proof.py` and `analyze_tier_authz.py` (Behavior 1);
  `coercion_calibration.py`, `coercion_label_testset.py`, `coercion_scoreboard.py`,
  `coercion_heldout_check.py` (Behavior 2); `smoke_test.py` (offline + deps checks).

## Set up to reproduce (Azure credentials required)

This is the one-time install path. After step 4 you are fully set up. The eval runs are
listed below as a reference — only invoke them when you are ready, since each call hits the
Azure model and takes 30–90 min.

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

```bash
python -m pip install -e ".[acs]"
python -c "import agent_control_specification; print('ACS OK')"
```

`agent-control-specification` publishes a prebuilt Linux wheel; on macOS and Windows pip
builds it from the sdist (auto-bootstraps Rust if needed — you only need a C linker). If it
will not build on your platform, the ACS arms fall back to `runtime/acs_shim.py`, which
dispatches **the same** Rego through the `opa` binary. Same policy, same engine, different
dispatcher — that is how the reported results were produced on Windows.

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

Required for the ACS arms of either behavior, for the KB UI's grounding gate, and for
`tests/test_tier_authorization_generalization.py` (which skips without it).

### 4. `.env` with Azure credentials

PowerShell (Windows):

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

bash (macOS / Linux):

```bash
[[ -f .env ]] || cp .env.example .env
```

Then open `.env` in an editor and fill in `AZURE_API_KEY` and `AZURE_API_BASE`.
(Coding assistants: do not echo or print the values you set.)

### Optional: Node 20+ for the viewer

```powershell
winget install OpenJS.NodeJS.LTS
```

```bash
brew install node
```

**Coding assistants: stop here.** The eval commands below are for the user to invoke when
they choose.

### Offline checks (no credentials, no cost)

```bash
pytest examples/bank_manager_agent_control/tests -q
python examples/bank_manager_agent_control/scripts/smoke_test.py
python examples/bank_manager_agent_control/scripts/generalization_proof.py
```

### Eval reference (user-invoked)

From the repo root in an activated venv. **Behavior 1** — arm 1 owns
`systematize → test_set → inference → judge`; arms 2 and 3 override only `run:` and the
target, so they reuse the frozen test set:

```bash
assert-ai run --config examples/bank_manager_agent_control/eval_tier_authorization.yaml

assert-ai run --config examples/bank_manager_agent_control/eval_tier_authorization.yaml \
  --override run=arm2-defensive-prompt \
  --override inference.target.callable=examples.bank_manager_agent_control.agent_tier_authz:chat_defensive_prompt_tier_authz

assert-ai run --config examples/bank_manager_agent_control/eval_tier_authorization.yaml \
  --override run=arm3-acs-rego \
  --override inference.target.callable=examples.bank_manager_agent_control.agent_tier_authz:chat_acs_rego_tier_authz
```

For judged numbers, prefer the connector config; select the arm with the
`TIER_AUTHZ_ARM_SELECT` environment variable (`arm1` / `arm2` / `arm3`) and give each run a
distinct `run:` value:

```bash
assert-ai run --config examples/bank_manager_agent_control/eval_tier_authorization_traced.yaml \
  --override run=arm1-baseline-traced
```

Set `TIER_AUTHZ_TELEMETRY=<path>.jsonl` to capture the per-turn deterministic enforcement
log that `scripts/analyze_tier_authz.py` reads.

**Behavior 2** — all four arms share one suite, so arms 2–3n hit the cached test set:

```bash
assert-ai run --config examples/bank_manager_agent_control/eval_coercion_authority.yaml
assert-ai run --config examples/bank_manager_agent_control/eval_coercion_arm2_hardened.yaml
assert-ai run --config examples/bank_manager_agent_control/eval_coercion_arm3_acs.yaml
assert-ai run --config examples/bank_manager_agent_control/eval_coercion_arm3n_naive.yaml
```

Refitting the classifier calibration (optional; hits the model) and re-deriving ground truth:

```bash
python examples/bank_manager_agent_control/scripts/coercion_calibration.py --model gpt-4o-mini --workers 8
python examples/bank_manager_agent_control/scripts/coercion_label_testset.py gpt-4o
```

Each run writes to `artifacts/results/<suite>/<run>/`. Result artifacts are not committed, so
nothing in the repo is overwritten by a run.

### Start the viewer

```bash
cd viewer && npm install && npm run dev   # then open http://localhost:5174
```

Open the `tier-authorization` or `bank-manager-coercion-authority` suite and compare the arms.
If the viewer hangs, stop the dev server, free the port, and restart it — it caches parsed
artifacts in memory and does not always notice a mid-run rewrite.

## How the ACS integration works

ACS is a stateless policy decision point. The host (this example) owns the agent loop and
acts as the policy enforcement point.

**Common to both behaviors:**

- `AgentControl.from_path(<manifest>)` loads the manifest and wires the OPA dispatcher.
  `bank_agent_common._acs_manifest_with_absolute_bundle` rewrites a relative `bundle:` path
  to an absolute one first — ACS 0.1.0 silently fails on Windows otherwise.
- Each MCP tool is wrapped with `control.run_tool(name, args, execute, snapshot=...)`, which
  bundles `pre_tool_call` + `post_tool_call` evaluation plus enforcement.
- ACS is stateless, so the host tracks per-turn state and threads it into each snapshot.
- Deny verdicts raise `AgentControlBlocked`; the wrapper re-raises as a LangChain
  `ToolException` so the agent receives the policy's refusal text as the tool's response.

**Behavior 1 — property-based policy, no annotator.** The manifest declares no `tools:` block
because there is nothing to register: the rule keys on `risk_tier` and `entity_id` carried by
every tool result, so a tool added tomorrow is covered the day it ships. It gates
`pre_tool_call` on state-changing calls (an unauthorized write never executes) and
`post_tool_call` on reads, and it **fails closed** on an unparseable result
(`unclassified_result`).

**Behavior 2 — classifier annotator (ACS §10).** The annotator is declared once and referenced
at an intervention point:

```yaml
annotators:
  coercion_risk:
    type: classifier
    module: coercion_classifier
    entrypoint: annotate
    calibration: ./runtime/coercion_calibration.json
    timeout_ms: 20000

intervention_points:
  pre_tool_call:
    policy:
      query: data.agent_control_specification.bank_manager_coercion.pre_tool_call_verdict
    annotations:
      coercion_risk:
        from: "$.snapshot.user_message"
```

The **host** owns dispatch (`runtime/acs_annotator_shim.py`) — ACS ships no classifier engine.
Output lands only at `annotations.coercion_risk`; annotators run in ascending lexicographic
order; errors and timeouts **fail closed** into the escalate band. The Rego is three-valued
(`deny` above `deny_hi`, `escalate` above `escalate_lo`, else `allow`) and its band defaults are
`2` — unreachable — so a missing annotation can never widen the allow band.
