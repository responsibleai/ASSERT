# Publishing ASSERT runs to Azure AI Foundry

This guide walks you end-to-end through publishing a completed
ASSERT run to a Microsoft Foundry project so it shows up in the
Foundry Evaluations UI with per-row scores, per-dimension pass/fail
counts, and (optionally) a Foundry-side LLM re-judgment beside
ASSERT's own verdict.

For the on-the-wire schema mapping (what gets uploaded, what
fields each Foundry asset uses, why), see
[`docs/integrations/foundry.md`](../integrations/foundry.md).

## What the pusher creates

Each `assert-ai foundry push` invocation registers three assets in
your Foundry project and one eval run:

- **Custom evaluators** (per ASSERT dimension) — either a
  code-based one that plucks the pre-computed ASSERT score off the
  row, a prompt-based one that re-judges the same row using
  Foundry's LLM, or both side-by-side (the default).
- **Dataset asset** — the ASSERT run's inference set + judge
  verdicts as newline-delimited JSON. Deterministic content
  hashing means re-pushing the same logical content reuses the
  existing dataset asset without a re-upload.
- **Eval + eval run** — the eval is reused by name across pushes
  of the same ASSERT suite; the run is created per push so the
  Foundry UI shows a trend line over time.

## Prerequisites

- **Foundry project**: an existing Foundry project you can read
  and write to. Any of Foundry's project shapes work (Hub-based,
  AI-Services-based, virtual).
- **RBAC**: your identity needs `Azure AI Developer` on the
  project (or equivalent — Dataset Write, Evaluator Write, Eval
  Write).
- **Judge deployment**: if you use the prompt variant (the default
  `both` mode enables it), the project needs a chat-capable model
  deployment. The runbook below uses `gpt-5.4-mini`; substitute
  your own deployment name.
- **Python**: 3.10+.
- **ASSERT run on disk**: a completed run directory under
  `artifacts/results/{suite_id}/{run_id}/` with a valid
  `manifest.json`, `scores.jsonl`, and `inference_set.jsonl`.

## Install

```bash
pip install "assert-ai[foundry]"
```

The `foundry` extra pulls in `azure-ai-projects>=2.2.0` and
`azure-identity>=1.19.0`. Nothing on the pusher touches Foundry
until you actually run `foundry push` — the subpackage is
lazy-imported so a base install stays lean.

## Authenticate

The pusher uses
[`DefaultAzureCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.defaultazurecredential),
so any credential source it supports works. The simplest for
interactive use:

```bash
az login
# If you have multiple tenants, pick the one your Foundry
# project lives in:
az account set --subscription <sub-id>
```

Non-interactive alternatives (managed identity, workload identity,
service principal, env vars) all work with the same command below.

## Identify your project

The `--project` flag takes any of these forms:

| Form | Example |
|------|---------|
| Full ARM ID | `/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<acct>/projects/<proj>` |
| `account/project` shorthand | `my-account/my-project` |
| Full endpoint URL | `https://<acct>.services.ai.azure.com/api/projects/<proj>` |

The ARM ID is the most robust — it lives on the project's
**Overview** blade in the Azure portal. Export it once so you
don't have to retype it:

```bash
export FOUNDRY_PROJECT="/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<acct>/projects/<proj>"
```

## Dry-run first

The dry-run mode resolves everything the real push would resolve —
evaluator specs, dataset row count and content hash, judge
deployment, eval name — but makes zero network calls. It's the
fastest way to catch config mistakes before you touch a real
Foundry project.

`--project` is optional in dry-run mode, so this works in a fresh
checkout before you've picked a project or run `az login`:

```bash
assert-ai foundry push artifacts/results/<suite>/<run> --dry-run
```

For a real push, `--project` is required and takes any of the
forms in the [Identify your project](#identify-your-project)
section above.

Expected output shape:

```
Dry-run -- no network calls made.

Eval name                   ASSERT: <suite>
Run name                    ASSERT run: <run>
Dataset name                assert-<suite>
Dataset version             <12-hex-content-hash>
Dataset rows                3
Judge model                 gpt-5.4-mini
Passing-when-true           (none)
LLM calls (prompt variant)  ~9 (1 per row x prompt evaluator)
                    Evaluators (6)
Name                             Variant  Fingerprint
assert-answer_quality            code     <12-hex>
assert-answer_quality-rescore    prompt   <12-hex>
assert-overrefusal               code     <12-hex>
assert-overrefusal-rescore       prompt   <12-hex>
assert-policy_violation          code     <12-hex>
assert-policy_violation-rescore  prompt   <12-hex>
```

The `LLM calls (prompt variant)` row shows the number of judge calls
Foundry will make on the real push (`prompt evaluators x rows`).
Under `--evaluator-mode code`, it renders `0 (code-only mode)`.

If any dimension in `scores.jsonl` doesn't have a rubric resolved
(no inline `pipeline.judge.dimensions.{name}` in `config.yaml` and
not one of the built-ins), the dry-run will still succeed but the
prompt-variant description falls back to a generic 1-5 rubric.
Prefer providing a real rubric for anything more nuanced than a
smoke test.

## First real push

```bash
assert-ai foundry push artifacts/results/<suite>/<run> \
    --project "$FOUNDRY_PROJECT" \
    --eval-name "ASSERT: <descriptive-name>"
```

`--eval-name` is optional but recommended for the first push
because it becomes the eval's display name in the Foundry UI. The
default (`ASSERT: <suite-id>`) works fine when your suite id is
already human-readable.

Expected output:

```
Eval     eval_<32-hex>
Run      evalrun_<32-hex>
Dataset  azureai://accounts/<acct>/projects/<proj>/data/assert-<suite>/versions/<hash>
                         Evaluators (6)
Name                             Version  Variant  Status
assert-answer_quality            v1       code     new
assert-answer_quality-rescore    v1       prompt   new
assert-overrefusal               v1       code     new
assert-overrefusal-rescore       v1       prompt   new
assert-policy_violation          v1       code     new
assert-policy_violation-rescore  v1       prompt   new
```

Foundry starts scoring immediately after the run is created. For a
few rows it typically completes within a minute; larger datasets
take proportionally longer for the prompt variant (one LLM call
per row × prompt-variant dimension).

## Watching a run complete

The Foundry UI on
[ai.azure.com](https://ai.azure.com/) → your project →
**Evaluation** → your eval name → the latest run auto-refreshes
`result_counts` and per-criterion pass/fail as scoring progresses.

For a CLI-friendly poll, use `openai_client.evals.runs.retrieve`
via a quick Python one-liner or (if you prefer curl) the
[Foundry REST endpoint](https://learn.microsoft.com/rest/api/aifoundry/aiprojects/eval-runs/get)
`GET /openai/v1/evals/{eval_id}/runs/{run_id}?api-version=2025-04-01-preview`.

A successful terminal state looks like:

```json
{
  "status": "completed",
  "result_counts": {"total": 3, "passed": 3, "failed": 0, "errored": 0, "skipped": 0},
  "per_testing_criteria_results": [
    {"testing_criteria": "policy_violation", "passed": 3, "failed": 0},
    {"testing_criteria": "policy_violation-rescore", "passed": 3, "failed": 0},
    ...
  ]
}
```

## Second push (same suite, new run)

Nothing to change. Just re-run the same command:

```bash
assert-ai foundry push artifacts/results/<suite>/<new-run> \
    --project "$FOUNDRY_PROJECT"
```

The pusher reuses the eval, reuses evaluators, and either reuses
or uploads a new dataset version depending on whether row content
changed:

```
Eval     eval_<same-hex-as-before> (reused)
Run      evalrun_<new-hex>
Dataset  azureai://.../versions/<same-hash> (reused)
                       Evaluators (6, 6 reused)
Name                             Version  Variant  Status
assert-answer_quality            v1       code     reused
assert-answer_quality-rescore    v1       prompt   reused
assert-overrefusal               v1       code     reused
assert-overrefusal-rescore       v1       prompt   reused
assert-policy_violation          v1       code     reused
assert-policy_violation-rescore  v1       prompt   reused
```

New rows or a new judge verdict shift the content hash, which
uploads a new dataset version and points the new run at it —
existing runs stay pinned to their original dataset version so
history is preserved.

## CLI reference

```
assert-ai foundry push RUN_DIR
    --project <arm-id | account/project | endpoint-url>   [required for real push]
    --evaluator-mode {code, prompt, both}                 [default: both]
    --eval-name <name>                                     [default: ASSERT: <suite_id>]
    --run-name <name>                                      [default: ASSERT run: <run_id>]
    --dataset-name <name>                                  [default: assert-<suite_id>]
    --passing-when-true DIM=TRUE|FALSE                     [repeatable]
    --judge-threshold <float>                              [default: 3.0]
    --dry-run
```

### `--evaluator-mode`

- **`both`** (default): register both the code variant and the
  prompt variant per dimension. The Foundry UI shows them
  side-by-side. Costs one LLM call per row × prompt dimension.
  Best for demos or when you want a Foundry-side second opinion
  on ASSERT's verdict.
- **`code`**: register only the code variant. Zero incremental
  judge cost — Foundry just plucks the pre-computed ASSERT score
  off each row. Best for production sharing.
- **`prompt`**: register only the prompt variant. Skips the ASSERT
  verdict entirely and asks Foundry's LLM to re-judge each row
  from `query` + `response`. Rarely what you want.

### `--passing-when-true`

Override which boolean value counts as "pass" for a custom
dimension. Built-in violation-flag dimensions
(`policy_violation`, `overrefusal`) are always
`True = fail = 0.0`, `False = pass = 1.0` and can't be overridden.

Rubric-positive custom dimensions (like `answer_quality` where
`True` means "the answer is good") need this override:

```bash
--passing-when-true answer_quality=true
```

Repeat the flag for each rubric-positive dimension. Missing dims
default to violation-flag semantics (`True = fail`).

### `--judge-threshold`

Score threshold (1-5) at which the prompt variant considers a
response "passing". Only affects prompt-variant evaluators;
ignored in `code` mode. Default is `3.0`, matching the built-in
rubrics' midpoint.

## Verify in the Foundry UI

- **Evaluation** → your eval → **Runs** — one row per push, with
  overall pass/fail counts, dataset version, and start time.
- **Runs** → your run → **Rows** — one row per ASSERT sample,
  with per-testing-criterion scores. Clicking a row shows the
  full `query`, `response`, `assert_scores`, and
  `assert_reasons`.
- **Evaluators** (project-level catalog) — your
  `assert-{dim}` and `assert-{dim}-rescore` evaluators show up
  here and can be reused by other evals if you want to combine
  ASSERT dimensions with Foundry's built-ins.
- **Datasets** — the `assert-<suite>` dataset shows every version
  you've uploaded and which runs reference each.

## Troubleshooting

### `EvaluatorNotFound` at eval-create time

The eval definition references an evaluator by short name
(`assert-{dim}`) but Foundry can't find it. Almost always caused
by an intermediate failure between evaluator registration and
eval creation. Re-running the same push usually fixes it — the
pusher is idempotent and will reuse whatever it managed to
register the first time.

### `Data mapping for required field 'item' is missing`

Foundry sometimes reports this on an eval-create attempt for a
name that was previously used to register an incompatible eval
shape (for example, a v1-era push that registered rubric
evaluators under the same eval name). The fix is to bump the eval
name so a fresh definition gets created:

```bash
assert-ai foundry push ... --eval-name "ASSERT: <suite>-v2"
```

The pusher itself validates the `data_mapping` regex locally
before submitting, so the message really is a Foundry-side stale
association.

### New evaluator versions appearing in the catalog

Each time the ASSERT-side spec for a dimension changes — most
commonly because you edited the rubric in `config.yaml` — the
pusher registers a new evaluator version rather than overwriting
the existing one. Old versions stay intact, so historical eval
runs pinned to them keep rendering their original grader body in
the Foundry UI. If you want a specific eval to score against the
new version, create a new eval by bumping `--eval-name`.

Detection is fingerprint-based: the pusher SHA-256s
`type + code_text + prompt_text + data_schema + init_parameters + metrics`
and reuses any existing version with a matching fingerprint. UI
prose (`description`, `display_name`) is excluded from the
fingerprint on purpose, so pure-prose edits don't churn versions.

To preview which evaluators would drift on the next push without
touching Foundry, dry-run with `--json` and diff the
`fingerprint` field across pushes:

```bash
assert-ai foundry push RUN --project P --dry-run --json > /tmp/fp.json
# edit config.yaml
assert-ai foundry push RUN --project P --dry-run --json > /tmp/fp-after.json
diff <(jq '.evaluators' /tmp/fp.json) <(jq '.evaluators' /tmp/fp-after.json)
```

### `Testing criteria drift on existing eval`

You have an eval with this name already, but its `testing_criteria`
differ from what the pusher wants to publish (different
dimensions, added `-rescore` variants, etc). Foundry's
`POST /evals/{id}` endpoint accepts name+metadata updates only —
not `testing_criteria` — so the pusher fails loudly instead of
publishing an inconsistent eval. Bump `--eval-name` to create a
fresh eval.

### Judge deployment errors

If prompt-variant scoring fails with a
`ResourceNotFound: deployment 'xxx' not found`, the deployment
name resolved from your ASSERT config doesn't match a deployment
on the underlying CogSvc / AI-Services account. Check:

1. The deployment name Foundry expects is the **bare** name
   (e.g. `gpt-5.4-mini`), not the LiteLLM-prefixed name
   (`azure/gpt-5.4-mini`). The pusher strips the prefix
   automatically, so this should Just Work.
2. The deployment exists on the account that hosts your project
   (portal → your CogSvc/AI-Services resource → **Deployments**).
3. Your identity has `Cognitive Services User` on that
   resource.

### RBAC errors on dataset upload

`AuthorizationFailed` on `startPendingUpload` usually means your
identity is missing `Storage Blob Data Contributor` on the
project's default storage account. The Azure AI Developer role
grants this transitively; if you're using a custom role, add
storage data-plane permissions explicitly.

## Related

- Schema mapping reference:
  [`docs/integrations/foundry.md`](../integrations/foundry.md)
- Getting started with ASSERT:
  [`docs/getting-started.md`](../getting-started.md)
- Foundry cloud evaluation docs:
  [Run evaluations in the cloud by using the Microsoft Foundry SDK](https://learn.microsoft.com/azure/foundry/how-to/develop/cloud-evaluation)
