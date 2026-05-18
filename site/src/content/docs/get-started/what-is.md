---
title: What is Adaptive Eval
description: Adaptive Eval is a spec-driven evaluation harness for AI agents — failure modes you define, test cases the pipeline generates, trace-grounded verdicts.
---

Adaptive Eval is a **spec-driven evaluation harness for AI agents**. You write a short plain-English spec describing what your agent should and should not do. The pipeline derives failure-mode categories, generates targeted single-turn and multi-turn test cases, executes them against your target, and uses an LLM judge to score each conversation against your spec.

Any agent or multi-agent system that runs in Python plugs in through `target.callable`. The recommended integration captures the agent's OpenTelemetry spans (Phoenix / OpenInference auto-instruments 33+ frameworks in two lines, or you can emit your own with the OTel SDK) so the judge can inspect tool calls, arguments, routing, latency, and intermediate decisions — not just the final response.

## Why it exists

Most eval tools treat agents as black boxes: text in, verdict out. That works for simple text apps. It does not work for the agents people are actually shipping in 2026 — multi-tool, multi-turn, frameworked, sometimes multi-agent. Those agents fail in ways that only show up in the *trace*: a wrong tool got called, a routing decision skipped a guard step, an injected payload from one tool propagated into another. Adaptive Eval is built around three premises:

1. **Specs, not benchmarks.** Generic safety benchmarks tell you almost nothing about your agent. Your spec — the rules your agent actually has to follow — is what should drive evaluation.
2. **Trace-grounded verdicts.** A judge with access to the full execution trace can cite *why* the agent failed, not just *that* the final answer was wrong.
3. **Framework-agnostic.** You should not have to change framework or rewrite your agent to evaluate it.

## What it produces

Adaptive Eval is a four-stage pipeline:

```
your eval spec (.md)
        │
        ▼
   failure_mode taxonomy (LLM-derived)
        │
        ▼
   test cases (single-turn + multi-turn scenarios)
        │
        ▼
   execute against your target  ← captures OTel traces
        │
        ▼
   judge each conversation → scores.jsonl + metrics.json
```

Every run lands under `artifacts/results/<suite>/<run>/`. The viewer reads those directly from disk.

## Where to go next

- [Quickstart](/adaptive-eval/get-started/quickstart/) — LangGraph travel planner end-to-end in ~10 minutes
- [Concepts](/adaptive-eval/get-started/concepts/) — spec, factors, taxonomy, judge dimensions
- [How it works](/adaptive-eval/learn/how-it-works/) — pipeline internals
- [Choose your target](/adaptive-eval/run/targets/) — callable, model+tools, HTTP, external
