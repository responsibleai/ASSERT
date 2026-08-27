# Bank support agent — setup and ACS integration reference

Start with the [example README](../README.md) for the two behaviors, measured
results, and runnable commands. This page documents setup and the policy
enforcement mechanics.

> If you arrived through an older pinned-commit or short link, use these current
> relative entry points: [example overview](../README.md),
> [AIEWF talk index](../../../talks/README.md), and
> [current deck](../../../talks/aiewf-18min/aiewf-2026-deck.pdf).
> Relative links avoid pinning future readers to a stale commit.

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
| Evidence | `target.callable` + OTel trace | OTel tool spans plus trace-visible `acs_policy` decisions |

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[acs,otel,langgraph,examples]"
Copy-Item examples/bank_manager_agent_control/.env.example .env
```

macOS/Linux:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[acs,otel,langgraph,examples]"
cp examples/bank_manager_agent_control/.env.example .env
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

It verifies the canonical UTF-8/LF fixture SHA-256 and class balance before
writing the suite-level `test_set.jsonl`.

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
after the result is available. Tier lookup is tri-state: missing records,
missing classifications, and unknown tier strings enter `unresolved_refs` and
deny state-changing calls before the tool runs. An unparseable result also
fails closed.

The required platform contract is explicit: every domain must emit the
normalized sensitivity property. Rego cannot repair forged or missing source
data. The six-domain script exercises direct policy inputs for two hypothetical
domains; it does not claim that the current host can resolve or serve those
domains end to end.

### Behavior 2: typed artifact verification + classifier annotator

The host rejects compound AUTH-/CB-/OPS-/CRD-/DA- tokens, verifies canonical
references against bank-owned state, and binds them to the concrete action
family, action instance, subject, exact destination/payee, amount scope,
session, and expiry before they can create an allow. A reference-shaped
substring, a real reference for another action or payee, or an expired record
never creates an allow. The pinned native ACS runtime invokes the host
classifier dispatcher and places both results in the ACS annotation. Rego
requires the verified binding to equal the canonical current-call binding,
maps invalid evidence or classifier uncertainty to escalation, and maps clear
coercion to deny.

The checked-in calibration fixture names `gpt-4o-mini`, but the historical
three-arm source runs did not commit their environment. The raw scorer's
held-out diagnostic outperformed the checked-in Platt calibration; a new run
should recalibrate on representative data, record every model role, and monitor
drift.

Every ACS verdict is emitted as a normal `acs_policy` OpenTelemetry tool span,
including the verified session, action context, and matched bank-owned
action-instance IDs. ASSERT's judge can cite that typed binding and the decision
alongside the bank tool call. The same span records the live non-secret
classifier deployment, calibration artifact SHA-256/schema version, and
threshold version.

## Run references

See the top-level README for full commands. Behavior 2 requires:

```bash
python examples/bank_manager_agent_control/scripts/prepare_powered_coercion.py
assert-ai run --config examples/bank_manager_agent_control/eval_coercion_authority.yaml

assert-ai run --config examples/bank_manager_agent_control/eval_coercion_authority.yaml \
  --override run=arm2-hardened-prompt \
  --override inference.target.callable=examples.bank_manager_agent_control.coercion_agent:chat_coercion_hardened_prompt

assert-ai run --config examples/bank_manager_agent_control/eval_coercion_authority.yaml \
  --override run=arm3-acs-calibrated-classifier \
  --override inference.target.callable=examples.bank_manager_agent_control.coercion_agent:chat_coercion_acs_classifier

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
