# ux-audit

## Purpose

Walk the ASSERT golden path and score each step for clarity, delight, friction, and error quality. The output is a compact audit table with one finding and one suggested fix per step.

## When to use

Use this skill before launch, after README or CLI changes, or when feedback suggests the first-run experience is unclear.

Run it from a **fresh worktree**. Record cold-install time separately from the
time to first useful result after dependencies are present. Do not use a
maintainer's existing `.env`, MCP config, running viewer, or remembered artifact
path.

## Golden path steps

1. **Install and verify the CLI**
   - Setup: `python -m pip install -e ".[otel,langgraph]"`
   - ASSERT command: `assert-ai --help`
2. **Wire required discovery tooling**
   - One pinned setup command; no manual second checkout.
   - Confirm the MCP tool is callable from the IDE.
3. **Write an eval spec**
   - ASSERT command: `assert-ai init --model azure/gpt-5.4 --describe "A customer-support chatbot with order lookup and refund tools" -o eval_config.yaml`
4. **Run a five-case smoke**
   - Prompt-only, scenario disabled, concurrency 5, run name `smoke`.
   - Must show stage progress and complete before proposing a long run.
5. **Read the output in the coding agent**
   - `assert-ai results status <suite> smoke`
   - `assert-ai results status <suite> smoke --json`
   - Inspect one cited failure before opening the viewer.
6. **Choose the full measurement**
   - Explain time/cost for 25 prompt + 25 scenario cases.
   - Ask before running; smoke rates are not evidence.

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
| Wire discovery tooling | 1-5 | 1-5 | 1-5 | 1-5 | Evidence-backed finding. | Smallest useful fix. |
| Write an eval spec | 1-5 | 1-5 | 1-5 | 1-5 | Evidence-backed finding. | Smallest useful fix. |
| Run the smoke eval | 1-5 | 1-5 | 1-5 | 1-5 | Evidence-backed finding. | Smallest useful fix. |
| Read terminal results | 1-5 | 1-5 | 1-5 | 1-5 | Evidence-backed finding. | Smallest useful fix. |
| Choose the full measurement | 1-5 | 1-5 | 1-5 | 1-5 | Evidence-backed finding. | Smallest useful fix. |

## Example audit row

| Step | Clarity | Delight | Friction | Error quality | 1-line finding | Suggested fix |
|---|---:|---:|---:|---:|---|---|
| Read the output | 4 | 3 | 4 | 2 | `assert-ai results status` shows metrics, but the next artifact to inspect is not obvious. | Print the path to `scores.jsonl` and `metrics.json` in the status output. |
