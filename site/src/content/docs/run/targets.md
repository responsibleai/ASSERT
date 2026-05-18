---
title: Choose your target
description: Decide which integration shape fits your agent.
---

`target` in `eval_config.yaml` tells the pipeline what to evaluate against. Four shapes are supported.

| Target | Use when | Observability |
|---|---|---|
| **`target.callable` + OTel auto-trace** | You have a framework agent (LangGraph, CrewAI, OpenAI Agents SDK, DSPy, LlamaIndex, AutoGen/MAF, …). **Recommended.** | 8 / 8 — tool calls, routing, framework internals, latency |
| **`target.callable` returning `ModelResponse`** | You have custom orchestration and can expose tool calls explicitly | 2 / 8 — tool calls (names + args), final text |
| **`target.callable` returning `str`** | You have a plain Python function | 1 / 8 — final text only |
| **`target.model` + `target.tools`** | Hosted model with a system prompt + a simple toolset, no framework | 2 / 8 |
| **`target.endpoint`** | Deployed HTTP API behind an auth token | 1 / 8 (text) — pair with callable wrapper + OTel for more |

## Recommended shape

For anything beyond a single-shot tool-calling model, use **`target.callable` + OpenTelemetry auto-instrumentation**:

```yaml
rollout:
  target:
    callable: examples.travel_planner_langgraph.auto_trace:chat_sync
    trace:
      backend: phoenix
      group_by: session.id
```

The agent code does not change. Two lines of Phoenix registration auto-instrument the framework. The judge sees every tool call, every routing decision, every LLM call.

## Why OTel matters

The same agent with the same spec produces different verdict quality depending on the target shape:

| Observable | OTel | ModelResponse callable | str callable |
|---|---|---|---|
| Final response text | ✅ | ✅ | ✅ |
| Tool calls + arguments | ✅ | ✅ (names/args only) | ❌ |
| Tool results | ✅ | ❌ | ❌ |
| Routing decisions | ✅ | ❌ | ❌ |
| Internal LLM calls | ✅ | ❌ | ❌ |
| Latency breakdown | ✅ | ❌ | ❌ |

A judge with full OTel evidence can cite *why* the agent failed. A judge with only the final text can only flag *that* something went wrong.

See:

- [Callable target](/adaptive-eval/run/callable/) — full callable integration guide
- [Model + tools target](/adaptive-eval/run/model-and-tools/) — hosted model integration
- [Travel planner agent flow](/adaptive-eval/run/travel-planner-flow/) — end-to-end OTel-enabled example
