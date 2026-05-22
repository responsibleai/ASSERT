---
title: How it works
description: The four-stage Adaptive Eval pipeline.
---

Adaptive Eval is a four-stage YAML-driven pipeline. Each stage reads the previous stage's artifact, runs an LLM step or the agent under test, and writes a deterministic artifact.

| Stage | Input | LLM step | Artifact |
|---|---|---|---|
| **Taxonomy** | spec `.md` | derive structured behavior categories | `taxonomy.json` |
| **Test cases** | `taxonomy.json` + context + dimensions | generate prompts + multi-turn scenarios | `test_set.jsonl` |
| **Execute** | `test_set.jsonl` + your `target` | drive conversations or agent actions, capture OTel | `inference_set.jsonl` |
| **Judge** | `inference_set.jsonl` + spec + dimensions | score each inference output with evidence | `scores.jsonl`, `metrics.json` |

## Stage 1 — Taxonomy

The taxonomy stage reads your spec and uses an LLM to derive a structured list of failure modes specific to your agent. Each failure mode has a name, description, and criteria the judge will later score against. This step is what makes the eval **targeted to your agent** rather than a generic benchmark.

## Stage 2 — Test cases

For each behavior category, the test-case generator produces single-turn prompts and multi-turn scenarios. Variations come from your `dimensions` block — each factor adds a dimension across which the generator diversifies.

Test cases are LLM-generated but cached by content hash. Re-running with the same spec, dimensions, and budget produces the same test cases.

## Stage 3 — Execute

The execute stage drives each test case against your `target`. For multi-turn scenarios, a *tester* LLM plays the user and the target plays the agent. OpenTelemetry spans are captured throughout — tool calls, framework internals, latency. They land alongside inference outputs (conversations or agent actions) and are linked into the judge prompt by `session.id` or `trace.id`.

## Stage 4 — Judge

The judge reads each inference output plus the OTel spans plus the spec, and scores against the configured dimensions (typically `policy_violation` and `overrefusal`). Each verdict carries a `reason` and **citations** — specific turn indices, tool-call IDs, or span IDs that support the verdict.

## Artifacts

Everything lands under `artifacts/results/<suite>/<run>/`. Two views:

- **CLI:** `p2m results show <suite> <run>` summarizes pass/fail per dimension, top failure modes, judge-failure rate.
- **Viewer:** `cd viewer && npm run dev` opens a browser UI that pages through conversations or agent-action traces with cited evidence highlighted.
