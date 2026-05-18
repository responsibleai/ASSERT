---
title: How it works
description: The four-stage Adaptive Eval pipeline.
---

Adaptive Eval is a four-stage YAML-driven pipeline. Each stage reads the previous stage's artifact, runs an LLM step or the agent under test, and writes a deterministic artifact.

| Stage | Input | LLM step | Artifact |
|---|---|---|---|
| **Taxonomy** | spec `.md` | derive structured failure modes | `taxonomy.json` |
| **Test cases** | `taxonomy.json` + context + factors | generate prompts + multi-turn scenarios | `test_cases.jsonl` |
| **Execute** | `test_cases.jsonl` + your `target` | drive conversations against the agent, capture OTel | `transcripts.jsonl` |
| **Judge** | `transcripts.jsonl` + spec + dimensions | score each transcript with evidence | `scores.jsonl`, `metrics.json` |

## Stage 1 — Taxonomy

The taxonomy stage reads your spec and uses an LLM to derive a structured list of failure modes specific to your agent. Each failure mode has a name, description, and criteria the judge will later score against. This step is what makes the eval **targeted to your agent** rather than a generic benchmark.

## Stage 2 — Test cases

For each failure mode, the test-case generator produces single-turn prompts and multi-turn scenarios. Variations come from your `factors` block — each factor adds a dimension across which the generator diversifies.

Test cases are LLM-generated but cached by content hash. Re-running with the same spec, factors, and budget produces the same test cases.

## Stage 3 — Execute

The execute stage drives each test case against your `target`. For multi-turn scenarios, an *auditor* LLM plays the user and the target plays the agent. OpenTelemetry spans are captured throughout — tool calls, framework internals, latency. They land alongside transcripts and are linked into the judge prompt by `session.id` or `trace.id`.

## Stage 4 — Judge

The judge reads each transcript plus the OTel spans plus the spec, and scores against the configured dimensions (typically `policy_violation` and `overrefusal`). Each verdict carries a `reason` and **citations** — specific turn indices, tool-call IDs, or span IDs that support the verdict.

## Artifacts

Everything lands under `artifacts/results/<suite>/<run>/`. Two views:

- **CLI:** `p2m results show <suite> <run>` summarizes pass/fail per dimension, top failure modes, judge-failure rate.
- **Viewer:** `cd viewer && npm run dev` opens a browser UI that pages through transcripts with cited evidence highlighted.
