# Local Viewer

ASSERT includes a local web app for browsing evaluation artifacts. It reads directly from `artifacts/results/` and supports suite browsing, run analysis, and live run monitoring.

The viewer reads from the filesystem on each request. There is no database or run-launch API.

## Prerequisites

- Node.js 18+
- Evaluation artifacts in `artifacts/results/` (from `assert-eval run`)

## Run in development

```sh
cd viewer
npm install
npm run dev
```

The dev server starts at `http://localhost:5174`.

## Build and preview

```sh
cd viewer
npm run build
npm run preview
```

## Type checking

```sh
cd viewer
npm run check
```

## What the viewer shows

- suite list with taxonomy and test-case counts
- taxonomy browser
- prompt browser (single-turn cases)
- scenario browser (multi-turn transcripts)
- run comparison views
- dimension breakdowns
- inference preview while runs are in progress
- live run monitor from `manifest.json`

## What the viewer does not do

- create eval configs
- launch pipeline runs
- provide authentication or access control

If you need access control, run it behind your own proxy.

## Code layout

- `src/lib/server/artifacts.ts`: artifact reads, path validation, and missing-vs-invalid handling
- `src/lib/server/data.ts`: page-facing view models
- `src/lib/server/metrics.ts`: prompt/scenario aggregates
- `src/lib/server/run-status.ts`: live monitor payloads from `manifest.json`
- `src/routes/*`: route handlers and page orchestration
- `src/lib/*`: shared UI helpers (citations, audit grouping, run polling, suite grouping)

## Required artifacts

The viewer expects this layout per suite:

```text
artifacts/results/<suite>/
├── taxonomy.json
├── systematization.json   # optional
├── test_set.jsonl
├── suite.json
└── <run>/
 ├── manifest.json
 ├── config.yaml
 ├── inference_set.jsonl
 ├── scores.jsonl
 ├── viewer_run_manifest.json        # completed judged runs
 ├── viewer_prompt_rows.json         # completed judged runs
 ├── viewer_audit_rows.json          # completed judged runs
 ├── viewer_transcript_index.json    # completed inferences
 └── viewer_score_index.json         # completed judged runs
```

Missing files expected for incomplete runs are handled where appropriate. Invalid JSON, JSONL, or YAML is treated as an artifact error and should be fixed or re-generated.

One exception exists for live inference: while `manifest.stages.inference == "running"`, the viewer tolerates one malformed trailing segment in `inference_set.jsonl` so it can read already-written rows before the current append finishes.

## Read-model behavior and refresh

Completed judged runs are served from run-level viewer read-model files, not by rescanning canonical JSONL on every request.

If `viewer_run_manifest.json` is missing or stale, rebuild by re-running judge for that run:

```bash
assert-eval run --config artifacts/results/<suite>/<run>/config.yaml --resume --force-stage judge
```

## Expected verdict contract

The viewer expects each successful score row to include:

- `verdict.dimensions` with binary event flags including `policy_violation` and `overrefusal`
- `verdict.dimension_justifications` for every dimension in `verdict.dimensions`
- `verdict.node_judgments` in taxonomy order with `node_name` matching `taxonomy.json` names
- `verdict.citations` used by inline `[N]` evidence markers

Rows that fail this strict contract (for example, `policy_compliance`-only rows) are not treated as valid scored judgments.

## Evidence drawer behavior

Explanation text can contain `[N]` citation chips that jump to cited transcript messages and highlight stored spans. Turn labels remain visible, but `Turn N` is not linkified, and the old separate `Evidence` block is not used for new structured artifacts.
