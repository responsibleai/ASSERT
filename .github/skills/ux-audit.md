# ux-audit

## Purpose

Walk the ASSERT golden path and score each step for clarity, delight, friction, and error quality. The output is a compact audit table with one finding and one suggested fix per step.

## When to use

Use this skill before launch, after README or CLI changes, or when feedback suggests the first-run experience is unclear.

## Golden path steps

1. **Install and verify the CLI**
   - Setup: `python -m pip install -e ".[otel,langgraph]"`
   - ASSERT command: `assert-ai --help`
2. **Write an eval spec**
   - ASSERT command: `assert-ai init --model azure/gpt-5.4 --describe "A customer-support chatbot with order lookup and refund tools" -o eval_config.yaml`
3. **Create a dataset of test cases**
   - ASSERT command: `assert-ai run --config eval_config.yaml --force-stage test_set`
4. **Run the eval**
   - ASSERT command: `assert-ai run --config eval_config.yaml`
5. **Read the output**
   - ASSERT command: `assert-ai results status travel-planner-langgraph-v1 demo-1`

## Scoring rubric for each dimension

| Dimension | 1 | 3 | 5 |
|---|---|---|---|
| Clarity | User cannot tell what to do next. | User can proceed after rereading or searching. | Next action, input, and output are obvious. |
| Delight | Step feels brittle or unrewarding. | Step works but has no useful feedback loop. | Step gives fast, confidence-building feedback. |
| Friction | Requires avoidable setup, guessing, or manual cleanup. | Some friction is present but bounded. | Minimal steps; no hidden prerequisites beyond documented credentials. |
| Error quality | Error is missing, noisy, or not actionable. | Error points to the area but not the fix. | Error names the cause and gives the next command or file to inspect. |

## Output format

| Step | Clarity | Delight | Friction | Error quality | 1-line finding | Suggested fix |
|---|---:|---:|---:|---:|---|---|
| Install and verify the CLI | 1-5 | 1-5 | 1-5 | 1-5 | Evidence-backed finding. | Smallest useful fix. |
| Write an eval spec | 1-5 | 1-5 | 1-5 | 1-5 | Evidence-backed finding. | Smallest useful fix. |
| Create a dataset of test cases | 1-5 | 1-5 | 1-5 | 1-5 | Evidence-backed finding. | Smallest useful fix. |
| Run the eval | 1-5 | 1-5 | 1-5 | 1-5 | Evidence-backed finding. | Smallest useful fix. |
| Read the output | 1-5 | 1-5 | 1-5 | 1-5 | Evidence-backed finding. | Smallest useful fix. |

## Example audit row

| Step | Clarity | Delight | Friction | Error quality | 1-line finding | Suggested fix |
|---|---:|---:|---:|---:|---|---|
| Read the output | 4 | 3 | 4 | 2 | `assert-ai results status` shows metrics, but the next artifact to inspect is not obvious. | Print the path to `scores.jsonl` and `metrics.json` in the status output. |
