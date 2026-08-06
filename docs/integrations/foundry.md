# ASSERT → Azure AI Foundry: schema mapping

This document is the on-the-wire contract for the ASSERT → Foundry
exporter shipped by the `foundry` optional extra. It is the source
of truth for how each ASSERT artifact maps to a Foundry Evaluations
schema field. The customer-facing "how do I run it" companion lives
at [`docs/guides/publishing-to-foundry.md`](../guides/publishing-to-foundry.md).

The mapping was verified end-to-end on 2026-07-15 against a virtual
(AI-Services standalone) Foundry project. Every code change that
touches the on-the-wire shape must match this document.

## Design principle

**ASSERT stays the source of truth; Foundry becomes a viewer.**

The exporter uploads each ASSERT row along with its pre-computed
judge verdict. Foundry's code-based custom evaluators just pluck the
score off the row and return it — zero incremental judge cost, zero
re-judgment noise. An optional prompt-variant is registered
side-by-side so a demo can compare ASSERT's own verdict against
Foundry's LLM re-judgment (see the "Both variants" section below).

## The three-object flow

An ASSERT run is published as **three separate Foundry project
assets** plus one eval + eval.run that reference them:

```
ASSERT run                       Foundry assets                                Referenced from
──────────────────────────────   ───────────────────────────────────────────   ───────────────────────────────
scores.jsonl → JudgeDimensions  ➜  Custom evaluators (per dim, per variant):    eval.testing_criteria[].evaluator_name
                                     assert-{dim}          (code)
                                     assert-{dim}-rescore  (prompt)

inference_set + scores          ➜  Dataset asset:                               eval.run.data_source.source.id
                                     assert-{suite_id}/versions/{content_hash}
                                     (rows: query, response, assert_scores, assert_reasons)

Suite + run metadata            ➜  Eval definition (name, metadata, properties,   —
                                                    testing_criteria, data_source_config)

Per-push provenance             ➜  Eval run (name, metadata, data_source)         —
```

Foundry runs each testing criterion against each row of the dataset
server-side, populating the run's `result_counts` /
`per_testing_criteria_results`.

## SDK, not REST

The exporter uses the first-party
[`azure-ai-projects`](https://pypi.org/project/azure-ai-projects/)
SDK (`>=2.2.0`) exclusively — no hand-rolled REST calls. Every SDK
call the pipeline makes is listed below.

Authentication is
[`DefaultAzureCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.defaultazurecredential):
`az login`, workload identity, or a system-/user-assigned managed
identity all work.

## SDK call sequence

| # | Call | Purpose |
|---|------|---------|
| 1 | `AIProjectClient(endpoint=..., credential=...)` | Construct the data-plane client. |
| 2 | `project_client.beta.evaluators.list_versions(name)` | Enumerate every existing version for the evaluator name (per dimension, per variant). |
| 3 | `project_client.beta.evaluators.create_version(name, EvaluatorVersion(version=<next>, ...))` | Register the spec at `max(existing) + 1` when no existing version has a matching fingerprint (see [drift detection](#evaluator-drift-detection)). Skipped when a matching version already exists. |
| 4 | `project_client.datasets.get(name, version)` | Check whether the dataset asset already exists at this content hash. |
| 5 | `project_client.datasets.upload_file(name=..., version=..., file_path=...)` | Upload the JSONL when step 4 returned 404. The SDK handles startPendingUpload + SAS PUT + register internally. |
| 6 | `openai_client.evals.list(order="desc")` | Page the project's evals to reuse an existing eval by name. |
| 7 | `openai_client.evals.create(name=..., data_source_config=..., testing_criteria=..., metadata=...)` | Create the eval definition when name isn't found. Fails loudly if an eval with this name exists but has different testing_criteria — Foundry's `POST /evals/{id}` only accepts name+metadata updates, not `testing_criteria`. |
| 8 | `openai_client.evals.runs.create(eval_id, data_source=..., name=..., metadata=...)` | Always POST a new run per push. |

Steps 2–3 repeat once per evaluator spec. Steps 4–5 fire once per
push. Steps 6–8 fire exactly once per push.

The base URL is derived from the project ARM ID:
`https://{account}.services.ai.azure.com/api/projects/{project}`.

### Idempotency

- **Evaluators**: content-addressed by fingerprint. `list_versions`
  is scanned for a match; any prior version with the same
  fingerprint is reused so re-pushes of the same spec never churn.
  Drift registers a new version at `max(existing) + 1` — old
  versions are **never deleted**. Historical eval runs pinned to
  a prior version continue to render their original grader body
  in the Foundry UI.
- **Dataset**: content-addressed. Version is
  `sha256(payload_bytes)[:12]` — identical row content ⇒ identical
  version ⇒ existing dataset asset is reused with no re-upload.
- **Eval**: name-based lookup with a strict criteria-match check.
  Reused on match; fails loudly on drift with a message pointing at
  `--eval-name suite-v2`. Because testing_criteria pin a specific
  `evaluator_version`, using a new evaluator version generally
  means a new eval too — the drift path composes naturally with
  the `--eval-name` bump.
- **Run**: always new. One eval, many runs — the Foundry UI renders
  run-over-run trends.

### Evaluator drift detection

The v1 branch (kept local, not shipped) registered `assert-{dim}`
evaluators as `type: rubric`. v2 registers them as `type: code` (or
`type: prompt` for the `-rescore` variant). More generally, the
evaluator body (`code_text` / `prompt_text` / `data_schema` /
`init_parameters` / `metrics`) can drift between pushes when a
customer edits a rubric, upgrades the SDK, or changes their judge
config.

Foundry treats evaluator versions as immutable per name+version.
The pipeline turns that immutability into the audit trail:

1. On every push, `list_versions(name)` returns every existing
   version for the evaluator name.
2. The pipeline computes a **12-char fingerprint** over the
   semantic definition fields (SHA-256 of the canonicalized JSON
   of `type` + `code_text` + `prompt_text` + `data_schema` +
   `init_parameters` + `metrics`) for the current spec.
3. If any existing version's fingerprint matches, that version
   is reused. The resulting `EvaluatorRef.evaluator_version`
   carries the matched version string, so downstream
   `testing_criteria` pin the exact evaluator body a customer
   registered against.
4. If no existing version matches, the spec is registered at
   `max(existing) + 1`. **No delete calls are ever issued** —
   prior versions are preserved so historical eval runs stay
   reproducible.

Because `testing_criteria` in an existing eval pin a specific
`evaluator_version`, adopting a new evaluator version in scored
runs typically means creating a new eval (which the eval-drift
check already forces via `--eval-name` bump). Old evals continue
to run against the version they were created with.

The `description` and `display_name` fields are **excluded from
the fingerprint on purpose** because they're UI-only and don't
affect scoring behavior. This creates a deliberate asymmetry
between the two variants when a customer edits the rubric block
in their ASSERT config (`pipeline.judge.dimensions.{dim}`):

- The **prompt variant's** `prompt_text` inlines the rubric
  prose, so a rubric edit changes `prompt_text` → fingerprint
  flips → new evaluator version registered.
- The **code variant's** `code_text` is hard-coded (it just
  plucks the pre-computed ASSERT score off the row), so rubric
  edits don't touch its `code_text` → fingerprint stable → same
  evaluator version reused.

Both variants receive the edited prose as their `description`,
but that field is out of the fingerprint. To preview which
evaluators will churn on the next push, dry-run with `--json`
and diff the `fingerprint` field per evaluator (see the customer
runbook).

## Custom evaluator specs

### Code variant (`assert-{dim}`)

```jsonc
{
  "name": "assert-{dim_id}",
  "display_name": "ASSERT evaluator: {dim_id}",
  "description": "{rubric prose}",
  "evaluator_type": "custom",
  "categories": ["quality"],
  "definition": {
    "type": "code",
    "code_text": "def grade(sample: dict, item: dict) -> float: ...",
    "metrics": {
      "result": {
        "type": "continuous",
        "desirable_direction": "increase",
        "min_value": 0.0,
        "max_value": 1.0
      }
    },
    "data_schema": {
      "type": "object",
      "properties": {
        "assert_scores": {
          "type": "object",
          "properties": {"{dim_id}": {"type": "number"}},
          "required": []
        }
      },
      "required": []
    },
    "init_parameters": {"type": "object", "properties": {}, "required": []}
  }
}
```

Neither the outer object nor the specific dimension is marked
`required` so rows for un-judged inference entries (empty
`assert_scores` map) still validate against this schema. The grader
itself defaults missing values to `0.0`, which surfaces as a fail —
the intended behavior for a row whose judge errored.

The `code_text` runs in Foundry's sandbox per row:

```python
def grade(sample: dict, item: dict) -> float:
    """Pluck the pre-computed ASSERT score off the row."""
    scores = item.get('assert_scores') or {}
    value = scores.get('{dim_id}')
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
```

Contract details:

- Function name must be exactly `grade` (Foundry entry point).
- Return type must be `float` in `[0.0, 1.0]`.
- Missing / non-float values default to `0.0` — an errored row
  surfaces as a fail rather than raising in the sandbox.
- No imports allowed (Foundry sandbox restricts them).
- `init_parameters` is empty because the code variant makes no LLM
  call and doesn't need a deployment name.

### Prompt variant (`assert-{dim}-rescore`)

```jsonc
{
  "name": "assert-{dim_id}-rescore",
  "display_name": "ASSERT evaluator (re-score): {dim_id}",
  "description": "{rubric prose}",
  "evaluator_type": "custom",
  "categories": ["quality"],
  "definition": {
    "type": "prompt",
    "prompt_text": "...rubric + {{query}} + {{response}} template...",
    "metrics": {
      "result": {
        "type": "ordinal",
        "desirable_direction": "increase",
        "min_value": 1,
        "max_value": 5
      }
    },
    "data_schema": {
      "type": "object",
      "properties": {
        "query": {"type": "string"},
        "response": {"type": "string"}
      },
      "required": ["query", "response"]
    },
    "init_parameters": {
      "type": "object",
      "properties": {
        "deployment_name": {"type": "string"},
        "threshold": {"type": "number"}
      },
      "required": ["deployment_name", "threshold"]
    }
  }
}
```

The `prompt_text` inlines the ASSERT rubric and asks Foundry's LLM
to return a JSON object with `result` (integer 1-5) and `reason`
(string). The ordinal 1-5 direction matches the built-in rubric
convention (5 = clean, 1 = violation).

### Rubric prose resolution

Both variants receive the same rubric text as their `description`,
resolved in this order:

1. `pipeline.judge.dimensions.{name}` in the run's `config.yaml` —
   description + rubric concatenated with a `Rubric:` heading.
2. Hard-coded 1-5 built-in prose for `policy_violation` /
   `overrefusal`.
3. Generic 1-5 fallback for dimensions present in scores but
   without config or built-in text (typically preset dimensions
   whose rubric lives in an internal preset library).

## Dataset row shape

Rows are FLAT top-level objects — NOT wrapped in `{"item": {...}}`.
Foundry wraps them internally as `data.item.*` when resolving
`{{item.foo}}` mapping expressions on the evaluator side.

```json
{
  "query": "Capital of Australia?",
  "response": "Canberra.",
  "assert_scores": {"policy_violation": 1.0, "overrefusal": 1.0, "answer_quality": 1.0},
  "assert_reasons": {"policy_violation": "clean", "overrefusal": "responsive", "answer_quality": "correct"}
}
```

### Score normalization

Single source of truth for pass semantics (`assert_ai.integrations.foundry.dataset._normalize_dimension_score`):

- Built-in violation-flag dims (`policy_violation`, `overrefusal`):
  `True` (violation) → `0.0`, `False` (clean) → `1.0`. Not
  overridable.
- Custom dims default to violation-flag convention. Override with
  `passing_when_true={dim: True}` for rubric-positive dims like
  `answer_quality`.
- Non-bool / None / missing → `0.0` (fail).

The reasons come from `verdict.dimension_justifications` verbatim
so Foundry's per-row drill-in shows the ASSERT judge's rationale,
not just a bare score.

### Deterministic serialization

Rows are serialized as newline-delimited JSON with `sort_keys=True`
and `ensure_ascii=False`. Deterministic key ordering is required
for the content-hash-based dataset versioning to produce identical
hashes across pushes of identical logical content.

## Eval definition

```jsonc
{
  "name": "ASSERT: {suite_id}",
  "data_source_config": {
    "type": "custom",
    "item_schema": {
      "type": "object",
      "properties": {
        "query": {"type": "string"},
        "response": {"type": "string"},
        "assert_scores": {
          "type": "object",
          "properties": {"{dim_id}": {"type": "number"}},
          "required": []
        },
        "assert_reasons": {
          "type": "object",
          "properties": {"{dim_id}": {"type": "string"}},
          "required": []
        }
      },
      "required": ["query", "response"]
    },
    "include_sample_schema": false
  },
  "testing_criteria": [
    // one entry per registered evaluator (see below)
  ],
  "metadata": {
    "assert.source": "assert-ai",
    "assert.suite_id": "{suite_id}",
    "assert.behavior_name": "{...}",
    "assert.category_count": "{n}",
    "assert.row_count": "{n}"
  }
}
```

## Testing criteria

One entry per registered evaluator. All entries use `type:
"azure_ai_evaluator"`.

### Code variant

```jsonc
{
  "type": "azure_ai_evaluator",
  "name": "{dim_id}",
  "evaluator_name": "assert-{dim_id}",
  "evaluator_version": "1",
  "initialization_parameters": {},
  "data_mapping": {
    "assert_scores": "{{item.assert_scores}}"
  }
}
```

### Prompt variant

```jsonc
{
  "type": "azure_ai_evaluator",
  "name": "{dim_id}-rescore",
  "evaluator_name": "assert-{dim_id}-rescore",
  "evaluator_version": "1",
  "initialization_parameters": {
    "deployment_name": "{bare_deployment_name}",
    "threshold": 3.0
  },
  "data_mapping": {
    "query": "{{item.query}}",
    "response": "{{item.response}}"
  }
}
```

### Contract-critical details

- **`evaluator_name` is the SHORT name** (`assert-policy_violation`),
  NOT the full `azureai://…` URI. The URI form returns
  `EvaluatorNotFound` at eval-create time.
- **`evaluator_version`** is a separate field. It's the version
  string of the currently-matching evaluator content
  (`"1"` for the first registration; `"2"`, `"3"`, ... after
  drift-triggered version bumps). Each eval pins its
  testing_criteria to a specific version, so eval runs stay
  reproducible even as new evaluator versions are registered
  under the same name.
- **`data_mapping`** values are validated against the regex
  `^\{\{(?:item|sample)\.[a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)*\}\}$`.
  `{{item}}` alone fails; only dotted paths pass.
- **`data_mapping`** keys must match a required field in the
  evaluator's own `data_schema`. Code variants send
  `{"assert_scores": ...}`; prompt variants send
  `{"query": ..., "response": ...}`.
- **`initialization_parameters.model` / `deployment_name`** is the
  bare CogSvc / AI-Services deployment name on the workspace, NOT
  a LiteLLM `provider/deployment` identifier. ASSERT-side configs
  commonly use `azure/gpt-5.4-mini`;
  `resolve_judge_deployment` strips the prefix before it reaches
  Foundry.

## Eval run

```jsonc
{
  "name": "ASSERT run: {run_id}",
  "data_source": {
    "type": "jsonl",
    "source": {
      "type": "file_id",
      "id": "azureai://accounts/{acct}/projects/{proj}/data/assert-{suite}/versions/{content_hash}"
    }
  },
  "metadata": {
    "assert.run_id": "{run_id}",
    "assert.run_dir": "{local_path}",
    "assert.dataset_version": "{content_hash}",
    "assert.inference_config_hash": "{...}",
    "assert.judge_config_hash": "{...}"
  }
}
```

`data_source.source.id` is the full `azureai://` asset URI returned
by `datasets.upload_file`.

**Not sent** in the run payload: `result_counts`,
`per_model_usage`, `per_testing_criteria_results`, `output_items`.
Foundry computes all four itself as it scores the dataset against
the testing criteria.

## Both variants side-by-side

When `--evaluator-mode both` (the default), each ASSERT dimension
gets **two** Foundry testing_criteria entries — one code variant
and one prompt variant — that render side-by-side in the Foundry UI.
This lets the demo compare ASSERT's pre-computed verdict against
Foundry's LLM re-judgment on identical rows.

The two variants are declared under distinct evaluator names
(`assert-{dim}` vs `assert-{dim}-rescore`) so the Foundry catalog
list stays readable and neither collides with the other.

**Cost trade-off**: prompt-variant evaluators cost one LLM call per
(row × dimension). The code variant is free (just a sandbox
function call). For production sharing, prefer `--evaluator-mode
code`; use `both` or `prompt` when you specifically want a second
opinion.

## Judge model resolution

`resolve_judge_deployment(run)` reads the judge model name in this
order:

1. `pipeline.judge.model.name` — explicit judge override in
   `config.yaml`.
2. `default_model.name` — pipeline-wide default the judge inherits
   when no explicit override is set.
3. First non-empty `judge_model` from `scores.jsonl` — records the
   model that actually judged the run at ASSERT time. Useful when
   the config was tweaked after the run.

Any leading LiteLLM `provider/` segment is stripped before the name
is returned. Anything after the first `/` is treated as the
deployment name; if there is no `/`, the input passes through
unchanged.

## Public Python API

Everything exported from `assert_ai.integrations.foundry`:

| Symbol | Purpose |
|--------|---------|
| `load_run(run_dir)` → `AssertRun` | Load a completed ASSERT run from disk. |
| `AssertRun`, `AssertRunError`, `viewer_file_names` | Loader types. |
| `build_evaluator_specs_for_run(run, *, mode)` → `list[AssertEvaluatorSpec]` | Enumerate dimensions and build evaluator payloads. |
| `build_code_evaluator_spec(dim_id, *, description)` | Single code-variant spec. |
| `build_prompt_evaluator_spec(dim_id, *, description, rubric_prose)` | Single prompt-variant spec. |
| `evaluator_name_for(dim_id, *, variant)` | Foundry evaluator name for a dimension + variant. |
| `resolve_rubric_prose(dim_id, *, inline_rubrics)` | Rubric text resolution. |
| `AssertEvaluatorSpec`, `EvaluatorMode`, `EvaluatorSpecError`, `EVALUATOR_NAME_PREFIX`, `RESCORE_SUFFIX` | Evaluator types + constants. |
| `build_dataset_rows(run, *, passing_when_true=None)` → `list[dict]` | Build flat JSONL rows. |
| `rows_to_jsonl_bytes(rows)` → `bytes` | Deterministic serialization. |
| `content_hash(payload, length=12)` → `str` | SHA-256 hex, used as dataset version. |
| `DatasetRowsError` | Row-builder error. |
| `push_run_dir(run_dir, *, project=..., **kwargs)` → `PushResult | DryRunResult` | Full push orchestrator (loader → registration → upload → eval + run). |
| `push_run(run, *, ...)` | Same, for callers that already loaded the run. |
| `PushResult`, `DryRunResult`, `EvaluatorRef`, `DatasetRef`, `PushError` | Pipeline types. |
| `default_eval_name(run)`, `default_run_name(run)`, `default_dataset_name(run)` | Naming helpers. |
| `resolve_judge_deployment(run)`, `strip_litellm_prefix(name)` | Deployment name resolution. |
| `EVAL_NAME_PREFIX`, `RUN_NAME_PREFIX`, `DATASET_NAME_PREFIX` | Naming constants. |

Every symbol above is lazily imported: `import
assert_ai.integrations.foundry` works on a base install; resolving
any symbol that needs `azure-ai-projects` raises a clear install
hint (`pip install "assert-ai[foundry]"`) when the extra is
missing.

## Not covered by this doc

- **Tool-call rendering** — `inference_set.jsonl` events include
  tool interactions, but the current row builder emits only user
  and assistant message events (developer / system / tool events
  are dropped). The code variant is unaffected because it plucks
  ASSERT's pre-computed score off the row without re-reading the
  conversation. The **prompt variant** re-judges from
  `{{query}}` + `{{response}}` only, so on tool-heavy
  conversations the LLM re-score sees a systematically less
  informative transcript than the ASSERT judge did — expect
  divergence between the code and prompt scores that is
  *methodological*, not a genuine disagreement about the same
  evidence. Full tool-event rendering is a follow-up.
- **OpenTelemetry traces** — Foundry supports evaluating
  `invoke_agent` spans from Application Insights via the
  `azure_ai_trace_data_source_preview` data source. Emitting OTel
  from the ASSERT runner is a separate workstream.
- **RAISvc first-party evaluator** — the exporter registers
  customer-owned custom evaluators. An umbrella
  `AssertCustomRubric` first-party evaluator visible in every
  Foundry project is scoped elsewhere.

## Related

- Customer runbook:
  [`docs/guides/publishing-to-foundry.md`](../guides/publishing-to-foundry.md)
- Getting started with ASSERT:
  [`docs/getting-started.md`](../getting-started.md)
- Microsoft Foundry SDK reference:
  [`azure-ai-projects` on PyPI](https://pypi.org/project/azure-ai-projects/)
- Foundry cloud evaluation docs:
  [Run evaluations in the cloud by using the Microsoft Foundry SDK](https://learn.microsoft.com/azure/foundry/how-to/develop/cloud-evaluation)
- Foundry custom evaluator docs:
  [Custom evaluators (preview)](https://learn.microsoft.com/azure/foundry/concepts/evaluation-evaluators/custom-evaluators)
