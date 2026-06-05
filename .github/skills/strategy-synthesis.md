# strategy-synthesis

## Purpose

Distill competitive-scan signals into targeted amendments for internal positioning docs. The output is a proposed unified diff, not a full rewrite.

## When to use

Use this skill after `competitive-scan` has produced enough high-quality signals to justify a small change in positioning, docs guidance, launch notes, or PM decision records.

Do not use it to regenerate sections wholesale. If the source material does not support a precise edit, return "no diff recommended" with the reason.

## Input

- Signals from the PM inbox
- Source URLs or citations attached to each signal
- Existing internal positioning doc path supplied by the PM agent
- Scope of requested amendment, such as docs wording, roadmap framing, or FAQ clarification

## Output format

Return only a proposed unified diff plus a short rationale.

```diff
--- a/path/to/doc.md
+++ b/path/to/doc.md
@@
-old sentence
+new sentence
```

Rationale: `<one or two sentences connecting the diff to observed evidence>`

## Methodology

1. Read the selected signals and group them by user job: eval spec, dataset, execute, judge, traces, artifacts, or CI.
2. Identify the smallest doc change that would address the repeated signal.
3. Preserve the existing document structure and voice. Do not rewrite unrelated paragraphs.
4. Draft a unified diff with narrow context and no speculative claims.
5. Check that terminology stays aligned: behavior, eval spec, dataset, test cases, execute, judge, OpenTelemetry, OpenInference trace attributes, spec-driven scoring.
6. If evidence is weak or stale, recommend no diff instead of forcing an edit.

## Output destination

Internal strategy docs only. The destination is configured outside this public repository by the PM agent. Do not write synthesis outputs to ASSERT public docs unless Chang explicitly approves a public-safe promotion.

## Example diff snippet

```diff
--- a/strategy/example.md
+++ b/strategy/example.md
@@
-ASSERT explains scoring through aggregate metrics first.
+ASSERT should explain scoring through judge evidence first, then aggregate metrics.
```

Rationale: Multiple public signals show that developers trust eval results faster when the score links back to concrete trace and response evidence.
