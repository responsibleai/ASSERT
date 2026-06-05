# competitive-scan

## Purpose

Scan public discussion and repository activity for signals that affect ASSERT's eval framework roadmap, docs, and positioning. The skill captures signals only. It does not write public posts, issues, PRs, or comments.

## When to use

Use this skill on a recurring schedule or before a launch milestone when the PM agent needs fresh external signal about eval framework expectations, adoption friction, or competing workflows.

## Sources to scan

- GitHub repositories: `promptfoo/promptfoo`, `Arize-ai/phoenix`, `confident-ai/deepeval`, `BerriAI/litellm`
- Hacker News threads about evals, agents, OpenTelemetry, tracing, and LLM testing
- Reddit: `r/LocalLLaMA`, `r/MachineLearning`, `r/mlops`
- X posts from maintainers, practitioners, and users discussing eval workflows

## Output format

| Signal | Source | Relevance to ASSERT | Suggested positioning response |
|---|---|---|---|
| Concrete observed signal. | URL, repo issue, thread, or post. | High / Med / Low. | One targeted response idea; no full rewrite. |

## Step-by-step methodology

1. Define the scan window and source list. Prefer recent, high-engagement, or maintainer-authored items.
2. Collect only public, citable signals. Do not include private conversations or non-public screenshots.
3. Classify each signal by user job: write eval spec, create dataset, execute target, judge outputs, inspect traces, compare runs.
4. Score relevance to ASSERT as High, Med, or Low based on product impact and evidence quality.
5. Write one suggested positioning response per signal. Keep it specific and testable.
6. De-duplicate repeated claims. Keep the clearest source as evidence and note that the signal repeated elsewhere.
7. Route the table to the internal PM inbox. Do not publish scan results in ASSERT docs, issues, PRs, or release notes.

## Output destination

Internal PM inbox only. The destination is configured outside this public repository by the agent runtime. Do not store competitive scan outputs in ASSERT.

## Example signal entry

| Signal | Source | Relevance to ASSERT | Suggested positioning response |
|---|---|---|---|
| Users ask for eval outputs that cite traces, not only final text. | Public discussion thread URL. | High | Emphasize trace-grounded judge evidence and show the artifact path from execute to judge. |
