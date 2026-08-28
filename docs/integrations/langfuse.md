# Export ASSERT results to Langfuse

The optional Langfuse bridge exports a completed ASSERT run for shared
inspection. **ASSERT produces the test cases, traces, and judgments. Langfuse
stores and visualizes those existing artifacts.** This integration does not
ask Langfuse to judge a trace or reproduce ASSERT's verdict logic.

## What it exports

| ASSERT artifact | Langfuse object | Public API |
|---|---|---|
| One `inference_set.jsonl` row | One trace with an OTLP root span | `POST /api/public/otel/v1/traces` |
| Boolean verdict dimension | Boolean score (`0` or `1`) | `POST /api/public/scores` |
| Numeric ordinal dimension | Numeric score | `POST /api/public/scores` |
| String ordinal dimension | Categorical score | `POST /api/public/scores` |

The root span carries the ASSERT events, owned LLM call records, stop reason,
run ID, test-case ID, behavior, and variation dimensions. The score comment
carries ASSERT's evidence-cited dimension justification. A dimension that
ASSERT explicitly marked not applicable is counted in the export summary but
is not posted as a score.

## Prerequisites

- A completed ASSERT run containing `inference_set.jsonl` and `scores.jsonl`.
- A Langfuse Cloud project, or a self-hosted deployment with the OTLP HTTP
  endpoint (introduced in Langfuse v3.22).
- Python 3.11+ and the normal ASSERT install. The bridge uses Python's standard
  library HTTP stack: it adds no core dependency and does not use the Langfuse
  SDK.

Review the artifacts before export. Transcripts and model call records can
contain user, model, or tool data; sending them to Langfuse moves that data to
the configured Langfuse project.

## Configure authentication

Use the current Langfuse environment variable names:

```bash
export LANGFUSE_BASE_URL="https://your-langfuse-origin.example"
export LANGFUSE_PUBLIC_KEY="<project-public-key>"
export LANGFUSE_SECRET_KEY="<project-secret-key>"
```

PowerShell:

```powershell
$env:LANGFUSE_BASE_URL = "https://your-langfuse-origin.example"
$env:LANGFUSE_PUBLIC_KEY = "<project-public-key>"
$env:LANGFUSE_SECRET_KEY = "<project-secret-key>"
```

`LANGFUSE_BASE_URL` must be an `http` or `https` origin without credentials or
an `/api/public` path. The bridge never reads `.env`, logs credentials, or
includes them in raised errors.

## Export a run

```python
from assert_ai.integrations.langfuse import LangfuseExporter, LangfuseHTTPClient

client = LangfuseHTTPClient.from_env()
summary = LangfuseExporter(client).export_run(
    "artifacts/results/<suite>/<run>"
)
print(summary)
```

Or run the checked-in synthetic example, which needs no model credential:

```bash
python examples/langfuse_bridge/run_bridge.py
```

Trace and score IDs are deterministic for a run ID, test-case ID, test-case
type, and dimension. Re-exporting the same run therefore addresses the same
Langfuse objects. The client deliberately performs no retries; callers see a
typed error and decide whether to resume or re-export.

## Errors

All integration exceptions derive from `LangfuseAdapterError`:

| Error | Meaning |
|---|---|
| `LangfuseConfigurationError` | Required environment names, URL, or timeout are invalid. |
| `LangfuseContractError` | Local artifacts are missing, malformed, unmatched, duplicated, or incomplete. |
| `LangfuseAuthError` | Langfuse returned `401` or `403`. |
| `LangfuseHTTPError` | Langfuse returned another unsuccessful status. |
| `LangfuseConnectionError` | The endpoint could not be reached. |
| `LangfuseResponseError` | A successful response was not a JSON object. |

Local artifact validation completes before the first network request. HTTP
failures can still leave a partial remote export; deterministic IDs make a
manual re-export safe.

## Tier-1 boundaries

This bridge exports only ASSERT-produced traces and scores. It does not add:

- an `assert-ai run` flag or automatic export;
- Langfuse evaluation rules or online re-judging;
- retries, dashboards, or feedback loops; or
- Langfuse evaluator definitions.

Evaluator registration is intentionally excluded. Langfuse's evaluator
creation API is currently marked unstable, and registering an
`llm_as_judge` evaluator would require Langfuse-side model and prompt
configuration that is not present in ASSERT's run artifacts. Posting the
result as an API score accurately preserves ownership: ASSERT made the
judgment, while Langfuse stores and visualizes it.

For a partner verification pass, run the synthetic example against both a
supported self-hosted deployment and Langfuse Cloud, then confirm that two
traces and their `policy_violation` and `overrefusal` scores appear. Do not add
live project credentials or exported customer data to the repository.

