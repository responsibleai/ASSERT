# Securing agents with ACS

ASSERT finds where an agent violates an eval spec; the ASSERT→ACS adapter turns those findings into a deployable Agent Control Specification (ACS) policy so the same failures can be blocked at runtime. The adapter summarizes per-behavior and per-dimension violation rates from `scores.jsonl`, the violated taxonomy nodes and their permissibility, and the intervention points and tools involved; reuses AGT's `acs-generator` to write `manifest.yaml`, a Rego bundle, and `report.md`; validates known-bad examples through the ACS Python SDK; and exposes `guard_target(...)` to re-run the same target secured.

ACS is AGT's stateless, deterministic, fail-closed policy runtime. It evaluates intervention points such as `input`, `output`, `pre_tool_call`, and `post_tool_call`, returning verdicts such as `allow`, `warn`, `deny`, `escalate`, or `transform`.

## Prerequisites

- Python 3.11+ and a working ASSERT checkout.
- `opa` on `PATH` for Rego evaluation.
- The optional ACS extra:

```bash
pip install -e ".[acs]"
```

Policy generation uses an LLM unless you pass a fake language model in Python. With the default `lm_kind="assert"`, the adapter uses ASSERT's LiteLLM configuration. Set provider credentials with environment variables such as `AZURE_API_KEY` and `AZURE_API_BASE`; never print or commit credential values.

`assert-ai acs eval-config` is different: it only reads an existing ACS manifest YAML and writes an ASSERT config. It does not require the optional ACS runtime extra, OPA, or model credentials just to render the config.

## Step 1: run an ASSERT eval

Start from the [getting started guide](../getting-started.md) or any existing eval config:

```bash
assert-ai run --config examples/travel_planner_langgraph/eval_config.yaml
```

ASSERT writes results under:

```text
artifacts/results/<suite>/<run>/
```

The ACS adapter reads the run's `scores.jsonl`, `inference_set.jsonl`, and suite-level `taxonomy.json`.

## Step 2: generate an ACS policy

Generate from a completed run:

```bash
assert-ai acs generate --suite <suite> --run <run> --out artifacts/acs/<suite>
```

Equivalent path-based selection:

```bash
assert-ai acs generate --run-dir artifacts/results/<suite>/<run> --out artifacts/acs/<suite>
```

Outputs:

- `artifacts/acs/<suite>/manifest.yaml` - ACS manifest consumed by the runtime/SDK.
- `artifacts/acs/<suite>/policy/<slug>.rego` - generated Rego policy.
- `artifacts/acs/<suite>/report.md` - generation summary, warnings, and `guarded_points`.

For ASSERT `policy_violation` findings, the adapter builds the guardrail prompt from structured findings signal only: the violated taxonomy node, its definition and permissibility, the per-node violation rate, the violated intervention points, and the names of any violating tools. Raw transcript text (assistant outputs, tool arguments and results, judge rationales) is deliberately not sent to the policy-generation model, so a runtime secret captured in a transcript cannot reach it. Generated rules are centered on the intervention points where ASSERT scored the violation and deny matching content at runtime.

`assert-ai acs generate` options:

- `--run-dir PATH` or `--suite TEXT --run TEXT`
- `--out PATH`
- `--min-rate FLOAT` (default `0.0`)
- `--min-count INT` (default `1`)
- `--model TEXT`
- `--lm-kind [assert|openai-compatible]` (default `assert`)
- `--strict/--no-strict`
- `--validate/--no-validate` (default `validate`)
- `--fail-on-allow`

The command writes `manifest.yaml`, `policy/<slug>.rego`, and `report.md` to `--out`.

## Step 3: validate the policy against ASSERT findings

Replay ASSERT's known-bad examples through ACS:

```bash
assert-ai acs validate --manifest artifacts/acs/<suite>/manifest.yaml --suite <suite> --run <run>
```

Validation reports two numbers: how many known-bad examples the policy `handled` (reacted to with `deny`, `escalate`, `warn`, or `transform`) and how many it `strongly blocked` (`deny`/`escalate`). A `warn` reacts but still permits execution, so it counts as handled but not as a block. Use `--fail-on-allow` in CI to fail if any known-bad example is allowed (no reaction), or the stricter `--require-block` to fail unless every known-bad example is strongly blocked.

`assert-ai acs validate` options:

- `--manifest PATH` (required)
- `--run-dir PATH` or `--suite TEXT --run TEXT`
- `--min-rate FLOAT`
- `--min-count INT`
- `--max-cases INT`
- `--fail-on-allow` (fail if any known-bad example is allowed; a warn still passes)
- `--require-block` (fail unless every known-bad example is strongly blocked)

## Step 4: generate a regression eval config from an existing ACS manifest

After a manifest exists, you can generate a small ASSERT config to check that the already-guarded target still enforces the known policy behavior and does not regress allowed behavior:

```bash
assert-ai acs eval-config \
  --manifest artifacts/acs/<suite>/manifest.yaml \
  --target-callable examples.my_agent:chat \
  --out eval_config.yaml
```

The generated config summarizes the manifest metadata, declared intervention points, tools, extends references, and rule metadata where present. It does not evaluate Rego, shell out to OPA, generate or modify ACS policy, or wrap the target. The `--target-callable` must point at the agent entrypoint that is already guarded by the ACS policy, either because your application attaches ACS or because you wrapped it manually with `guard_target(...)`.

Use this as a regression/sanity layer, not as the primary way to design an eval suite. Black-box behavior evals should still define the desired agent behavior and threat model first; policy-derived configs verify an existing or generated manifest afterward.

The generated judge dimensions are:

- `guardrail_policy_violation` - forbidden behavior passed through despite the policy.
- `allowed_request_regression` - allowed behavior was blocked, degraded, or overrefused.
- `policy_gap` - the failure appears caused by missing, ambiguous, too narrow, or too broad policy design.

## Step 5: re-run the target with Python guarding

Runtime guarding is a Python API, not a CLI command:

```python
from assert_ai.integrations.acs import load_findings, generate_policy, validate_policy, guard_target

summary = load_findings("artifacts/results/<suite>/<run>", min_rate=0.2)
artifacts = generate_policy(summary, out_dir="artifacts/acs/<suite>")   # manifest_path, rego_path, report_path, guarded_points
report = validate_policy(artifacts.manifest_path, summary)              # report.ok, report.handled, report.total
guarded = guard_target(my_agent_callable, artifacts.manifest_path)      # async; raises AgentControlBlocked on deny
```

Handle a blocking verdict:

```python
from agent_control_specification import AgentControlBlocked

try:
    response = await guarded("user request")
except AgentControlBlocked:
    response = "I can't complete that request."
```

Use shadow mode when you want ACS verdicts without blocking the target:

```python
guarded = guard_target(my_agent_callable, artifacts.manifest_path, mode="evaluate_only")
```

By default, `generate_policy` builds a language model from `lm_kind` and `model`. `lm_kind="assert"` uses ASSERT's LiteLLM provider configuration; set provider environment variables such as `AZURE_API_KEY` and `AZURE_API_BASE`. For offline or repeatable tests, pass a fake model response:

```python
from assert_ai.integrations.acs import build_language_model

language_model = build_language_model("fake", responses=[plan])
artifacts = generate_policy(summary, out_dir="artifacts/acs/<suite>", language_model=language_model)
```

## Step 6: deploy the ACS bundle

Deploy the generated `manifest.yaml` plus `policy/<slug>.rego` with AGT's ACS runtime/SDK (`agent-control-specification`). The same manifest used by `validate_policy(...)` and `guard_target(...)` is the runtime entry point. OPA is required for evaluating the generated Rego.

## Notes and limits

- Generation uses an LLM, so review the generated Rego and `report.md` before deploying.
- Findings are attributed to the taxonomy node the judge marked violated (`node_judgments`), not the behavior the test case was generated under, so a guardrail uses the definition and permissibility of the behavior that was actually violated.
- The guardrail prompt instructs the generator to block the general class described by each behavior definition rather than overfitting to one example, and to prefer a semantic classifier or LLM annotator over brittle keyword matching. Still review the generated Rego to confirm the class was captured without over-denying permissible content.
- The adapter extracts output, tool-call, and tool-result violations into the matching ACS intervention points (`output`, `pre_tool_call`, `post_tool_call`), and input violations into `input`. The Python `guard_target` re-run path only enforces the `input` and `output` points; tool-call guardrails are written into the manifest for a full ACS host that wraps the agent's tool calls.
- Policy-derived eval configs are regression checks for existing manifests, not a substitute for black-box eval design from desired behavior and the threat model.
- `assert-ai acs eval-config` summarizes manifest metadata only. It does not resolve `extends`, evaluate Rego, inspect generated policy bundles, or prove runtime enforcement by itself; run the generated ASSERT config against a guarded target to validate behavior.
- Representative outputs and judge rationales are not sent to the external generation model; the guardrail prompt is built from structured findings signal only. For sensitive evaluations, generate with a local or self-hosted model (`--lm-kind openai-compatible` pointed at your own endpoint, or a fake model in tests) to avoid sending ASSERT artifact metadata to a third-party provider.
- Credentials belong in environment variables only. Do not write provider keys into eval configs, generated policies, reports, or logs.
- Validation proves the policy reacts to ASSERT's known-bad examples; keep ASSERT in the regression loop as the eval spec and target evolve.
