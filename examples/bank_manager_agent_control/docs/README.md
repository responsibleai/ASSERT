# Bank support agent — setup and ACS integration reference

Start with the [example README](../README.md) for the two behaviors, measured
results, and runnable commands. This page documents setup and the policy
enforcement mechanics.

## Safety and credentials

- Never read, print, or commit `.env`.
- Required model-provider variable names are documented in
  [`.env.example`](../.env.example).
- The model-backed eval commands cost API credits. Run them only when the user
  explicitly asks.
- Curated fixtures under `fixtures/` are public synthetic test data. Generated
  inference and score artifacts stay under `artifacts/` and are gitignored.

## Components

| Component | Behavior 1 | Behavior 2 |
|---|---|---|
| Requirement | Sensitive tier requires authorization | Claimed authority cannot replace recorded evidence |
| Suite | `tier-authorization` | `bank-manager-coercion-powered-120` |
| Target | `agent_tier_authz.py` | `coercion_agent.py` |
| Control | `tier_authorization.rego` | classifier annotator + `bank_manager_coercion.rego` |
| Test data | Generated once, reused across arms | Reviewed 120-prompt fixture |
| Evidence | `target.callable` + OTel trace | Full inference transcript and gate audit |

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[acs,otel,langgraph,examples]"
Copy-Item .env.example .env
```

macOS/Linux:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[acs,otel,langgraph,examples]"
cp .env.example .env
```

The ACS policies require an `opa` binary on `PATH`:

```powershell
winget install open-policy-agent.opa
```

```bash
brew install opa
```

## Offline validation

```bash
python examples/bank_manager_agent_control/scripts/smoke_test.py
python examples/bank_manager_agent_control/scripts/generalization_proof.py
pytest examples/bank_manager_agent_control/tests -q
```

`prepare_powered_coercion.py` is also offline:

```bash
python examples/bank_manager_agent_control/scripts/prepare_powered_coercion.py
```

It verifies the fixture SHA-256 and class balance before writing the suite-level
`test_set.jsonl`.

## Trace capture

Behavior 1's config uses:

```yaml
target:
  callable: examples.bank_manager_agent_control.agent_tier_authz:chat_baseline_tier_authz
  trace:
    backend: otel
    group_by: session.id
```

ASSERT installs the available OpenInference instrumentors and captures the
LangGraph execution through OpenTelemetry. The judge can inspect model calls,
tool calls/results, and ordering. This is the recommended integration path for
any real agent or multi-agent system.

## ACS enforcement

The target host is the policy-enforcement point. ACS is the policy-decision
point.

### Common flow

1. Load the ACS manifest.
2. Wrap each tool call with `control.run_tool(...)`.
3. Supply the per-turn snapshot required by the policy.
4. Evaluate `pre_tool_call` and `post_tool_call`.
5. Return a denial/escalation as a tool result the agent and trace can see.

### Behavior 1: property-based Rego

The rule reads normalized `entity_id` and `risk_tier` from tool results.
State-changing actions are gated before execution; sensitive reads are filtered
after the result is available. An unparseable result fails closed.

The required platform contract is explicit: every domain must emit the
normalized sensitivity property. Rego cannot repair forged or missing source
data.

### Behavior 2: classifier annotator

No typed field identifies coercion. The host invokes the classifier annotator
on the request and tool context, then places the score in the ACS snapshot.
Rego maps the score into allow, escalate, or deny bands.

The published arm uses the calibrated scorer from the measured run. The
held-out diagnostic shows the raw scorer generalized better than the Platt
calibration; production deployment should recalibrate on representative data
and monitor drift.

## Run references

See the top-level README for full commands. Behavior 2 requires:

```bash
python examples/bank_manager_agent_control/scripts/prepare_powered_coercion.py
assert-ai run --config examples/bank_manager_agent_control/eval_coercion_authority.yaml
assert-ai run --config examples/bank_manager_agent_control/eval_coercion_arm2_hardened.yaml
assert-ai run --config examples/bank_manager_agent_control/eval_coercion_arm3_acs.yaml
python examples/bank_manager_agent_control/scripts/coercion_scoreboard.py
```

Each run writes to `artifacts/results/<suite>/<run>/`.

## Production hardening

- Pin and integrity-protect the normalized sensitivity signal.
- Fail closed or escalate when the policy engine is unavailable.
- Calibrate the classifier on representative traffic and monitor drift.
- Exercise the escalation band.
- Apply privacy, retention, and access controls to captured traces.
- Set CI thresholds on both impermissible and permissible behavior.
