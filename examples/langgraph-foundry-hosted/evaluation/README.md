# Foundry custom evaluation with ASSERT judge contracts

This adapter takes ASSERT-generated test cases or inference transcripts and runs
the same judge dimensions as versioned Microsoft Foundry custom prompt
evaluators. It demonstrates three Foundry-native routes without changing
ASSERT's core pipeline:

| Route | Foundry evaluates | Use when |
|---|---|---|
| [Native agent target](#a-native-agent-target) | Responses or Invocations from a Foundry prompt/hosted agent | Foundry can synchronously invoke the registered agent. |
| [Native trace target](#b-native-trace-target) | Existing `invoke_agent` OpenTelemetry in Application Insights | The agent is external, asynchronous, or otherwise cannot be invoked by the evaluation service. |
| [Precomputed dataset target](#c-precomputed-dataset-target) | ASSERT's existing target-visible inference transcripts | ASSERT already ran the target and you want Foundry reporting without replay. |

All routes use `DefaultAzureCredential`. They never read or upload
`scores.jsonl`.

## Install

Run ASSERT from the repository environment first. Install the example-local
Foundry dependencies separately:

```bash
python -m pip install -r examples/langgraph-foundry-hosted/evaluation/requirements.txt
```

The requirements follow the current stable SDK surface:

- `azure-ai-projects>=2.6.1,<3`
- `azure-identity>=1.15,<2`
- `openai>=3,<4`

`tests/test_sdk_contracts.py` is intentionally run with those example-local
requirements installed. It instantiates and serializes the official
`EvaluatorVersion`, `TestingCriterionAzureAIEvaluator`, and JSONL run models.
The Azure-only target, simulation, and trace extensions are not exposed as
public OpenAI request models, so the tests send their exact dictionaries
through the installed OpenAI client's request serializer and inspect the
resulting local HTTP body without making a cloud call.

Cloud mode uses these environment variable names:

| Variable | Purpose |
|---|---|
| `AZURE_AI_PROJECT_ENDPOINT` | Foundry project endpoint used by current evaluation docs. |
| `FOUNDRY_PROJECT_ENDPOINT` | Existing example variable; accepted as a fallback for the same endpoint. |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Judge model deployment and scenario simulator. |
| `AZURE_AI_SIMULATOR_MODEL_DEPLOYMENT_NAME` | Optional scenario simulator deployment when it differs from the judge. |
| `FOUNDRY_AGENT_NAME` | Native agent target or trace filter. |
| `FOUNDRY_AGENT_VERSION` | Optional pinned agent version. |

Use the existing example configuration guidance in the parent
[README](../README.md). This adapter does not add another `.env.example`.

## Judge contract and versioning

The adapter resolves `pipeline.judge` through ASSERT's current config loader,
including built-ins, presets, inline overrides, and disabled built-in
dimensions. Each enabled boolean dimension becomes a fingerprinted Foundry
prompt evaluator.

ASSERT boolean dimensions use **true = bad event occurred**. Foundry reports
binary metrics as pass/fail, with higher/true as desirable. The adapter makes
that inversion explicit:

```text
ASSERT true  (bad event) -> Foundry result false (fail)
ASSERT false (no event) -> Foundry result true  (pass)
```

The original description and rubric are embedded verbatim in the evaluator
prompt and the inversion is recorded in evaluator metadata. Ordinal dimensions
and dimensions that permit a null/not-applicable verdict fail with a precise
unsupported-contract error; the adapter does not invent replacement semantics.

Foundry currently requires a custom prompt evaluator version to support exactly
one evaluation level. The adapter therefore registers level-specific `turn` and
`conversation` versions with the same ASSERT rubric and semantic fingerprint.
Rubric drift creates a new version. Matching fingerprints are reused, and the
authoritative version returned by `create_version` is used in testing criteria.

Datasets use a deterministic 50-character `sha256-<43 hex>` version derived
from the full content hash, matching Foundry's dataset-version limit. The full
SHA-256 remains in lineage and run metadata. An exact version is reused;
conflicting name/version identity fails. Evaluations themselves are always
created fresh so changed testing criteria cannot silently reuse a stale
evaluation definition. Nothing is deleted or overwritten.

## Prepare without cloud writes

Every command supports `--dry-run` (alias `--prepare`). It validates artifacts,
compiles evaluators, and writes deterministic JSONL plus lineage under the
ignored `evaluation/output/` directory, but creates no Foundry resources.

```bash
python examples/langgraph-foundry-hosted/evaluation/run_native_target.py --dry-run
python examples/langgraph-foundry-hosted/evaluation/run_precomputed.py --dry-run
python examples/langgraph-foundry-hosted/evaluation/run_traces.py \
  --level conversation \
  --conversation-id <conversation-id> \
  --dry-run
```

Use `--test-set` and `--inference-set` to point at artifacts outside the default
`artifacts/results/<suite>/<run>/` layout.

## A. Native agent target

First generate `test_set.jsonl` with ASSERT. The adapter then splits prompt and
scenario rows automatically:

```bash
assert-ai run --config examples/langgraph-foundry-hosted/eval_config.yaml

python examples/langgraph-foundry-hosted/evaluation/run_native_target.py \
  --agent-name <foundry-agent-name> \
  --agent-version <version> \
  --protocol responses \
  --model-deployment <judge-deployment> \
  --simulator-model <scenario-simulator-deployment>
```

Prompt rows use Foundry's `azure_ai_target_completions` data source and
`azure_ai_agent` target. Each row is sent as one native target interaction.
Hosted agents using only `/invocations` use:

```bash
python examples/langgraph-foundry-hosted/evaluation/run_native_target.py \
  --agent-name <foundry-agent-name> \
  --protocol invocations
```

Scenario rows run separately through Foundry's current
`conversation_gen_preview` simulation path and conversation-level evaluators.
The command prints a separate report URL for prompts and scenarios. The preview
path requires a Foundry-registered agent target and a simulator model; it does
not provide a faithful route for arbitrary unregistered external agents. Use
the trace route for those agents instead of degrading a scenario into one
prompt.

Source rows retain ASSERT test-case ID, type, behavior, dimensions, suite/run
lineage, and per-row content hash. Run metadata records the route, dataset hash,
and evaluator-criteria hash.

## B. Native trace target

Foundry does **not** invoke a registered external agent in this mode. It
evaluates existing `invoke_agent` telemetry and does not replay requests.

Evaluate explicit trace IDs at turn level:

```bash
python examples/langgraph-foundry-hosted/evaluation/run_traces.py \
  --level turn \
  --trace-id <w3c-trace-id> \
  --trace-id <another-trace-id>
```

Evaluate explicit conversations:

```bash
python examples/langgraph-foundry-hosted/evaluation/run_traces.py \
  --level conversation \
  --conversation-id <conversation-id> \
  --conversation-end-time 2026-09-18T12:30:00Z
```

Conversation-ID lookup accepts an optional ISO-8601 `--conversation-end-time`
with an explicit timezone. Agent-filter windows use the separate integer Unix
epoch flags shown below; the command rejects time arguments from the wrong
source mode.

Sample an agent in a time window:

```bash
python examples/langgraph-foundry-hosted/evaluation/run_traces.py \
  --level conversation \
  --agent-name <agent-name> \
  --agent-version <version> \
  --agent-start-time <unix-seconds> \
  --agent-end-time <unix-seconds> \
  --max-traces 50 \
  --filter-strategy smart_filtering
```

For turn-level agent sampling, Foundry's documented `azure_ai_traces` source
uses a rolling lookback rather than anchored start/end timestamps:

```bash
python examples/langgraph-foundry-hosted/evaluation/run_traces.py \
  --level turn \
  --agent-name <agent-name> \
  --agent-version <version> \
  --lookback-hours 24 \
  --max-traces 50
```

Turn evaluation maps `query`, `response`, and tool-call evidence extracted by
Foundry. Conversation evaluation maps the reconstructed `messages` array.
Application Insights must contain GenAI semantic-convention spans with
`gen_ai.operation.name="invoke_agent"` and input/output message attributes.

This route is intentionally described as lossy relative to ASSERT's local
full-span judge: Foundry reconstructs evaluator inputs from documented
`invoke_agent` attributes, while ASSERT can judge its complete persisted
target-visible event stream. Trace evaluation also cannot recover requests or
evidence that were never emitted.

## C. Precomputed dataset target

This route reads only `test_set.jsonl` and `inference_set.jsonl`:

```bash
python examples/langgraph-foundry-hosted/evaluation/run_precomputed.py
```

The adapter:

1. strictly joins rows by `test_case_id`, independent of file order;
2. rejects duplicate, missing, or extra IDs, empty target outputs, and stale
   rows whose type, behavior, dimensions, source-case identity, or available
   source hash no longer matches `test_set.jsonl`;
3. renders the target-visible transcript and tool evidence with ASSERT's
   transcript helpers;
4. recursively sanitizes structured tool arguments/results and excludes raw
   provider payloads, `llm_calls`, credential fields and credential-bearing
   URLs, Windows/Unix absolute local paths, and hidden runtime metadata;
5. uploads a content-addressed joined JSONL dataset;
6. reuses the same turn-level custom evaluator versions;
7. creates a fresh Dataset-target evaluation, polls to a terminal state, and
   prints the portal `report_url`.

`scores.jsonl` may be absent, malformed, or present; it is never opened.

## Polling and failures

Polling is bounded and handles `completed`, `failed`, `canceled`, and timeout
states. Service errors retain actionable status/detail while endpoint and
credential-like text is removed. The scripts never call delete APIs.

## Historical lineage

This example carries forward the public engineering lessons from
[responsibleai/ASSERT#267](https://github.com/responsibleai/ASSERT/pull/267):
strict artifact validation, deterministic joins/JSONL, content hashes,
evaluator drift checks, dry-run behavior, fake clients, no-delete behavior, and
offline tests. #267 uploaded precomputed ASSERT scores and reasons; **this
adapter does not use that score-plucking architecture**. It recompiles the
ASSERT judge contract as Foundry custom evaluators.

Related public work:

- [responsibleai/ASSERT#250](https://github.com/responsibleai/ASSERT/pull/250) —
  the hosted LangGraph example.
- [responsibleai/ASSERT#252](https://github.com/responsibleai/ASSERT/pull/252)
  and [responsibleai/ASSERT#256](https://github.com/responsibleai/ASSERT/pull/256) —
  Foundry agent target/authentication and documentation lineage.
- [responsibleai/ASSERT#336](https://github.com/responsibleai/ASSERT/pull/336) —
  example-local optional dependency ownership.
- [responsibleai/ASSERT#63](https://github.com/responsibleai/ASSERT/pull/63),
  [responsibleai/ASSERT#308](https://github.com/responsibleai/ASSERT/pull/308),
  and [responsibleai/ASSERT#345](https://github.com/responsibleai/ASSERT/pull/345) —
  trace capture and existing-trace evaluation lineage.

Current Foundry API references:

- [Cloud evaluation overview](https://learn.microsoft.com/azure/foundry/observability/how-to/cloud-evaluation)
- [Model and agent targets](https://learn.microsoft.com/azure/foundry/observability/how-to/cloud-evaluation-targets)
- [Conversation simulation](https://learn.microsoft.com/azure/foundry/observability/how-to/cloud-evaluation-simulate-conversations)
- [Deployed interactions](https://learn.microsoft.com/azure/foundry/observability/how-to/cloud-evaluation-deployed-interactions)
- [Deployed conversations](https://learn.microsoft.com/azure/foundry/observability/how-to/cloud-evaluation-deployed-conversations)
- [Custom evaluators](https://learn.microsoft.com/azure/foundry/concepts/evaluation-evaluators/custom-evaluators)
