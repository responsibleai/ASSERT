---
title: Model + tools target
description: Use a hosted model with a system prompt and optional tools.
---

If your agent is a hosted LLM with a system prompt and an optional simple tool set (no orchestration framework), you can use `target.model` directly without writing a callable wrapper.

## Hosted model only

```yaml
rollout:
  target:
    model:
      name: azure/gpt-5.4-mini
      max_tokens: 4000
    system_prompt: |
      You are a travel-planning assistant. ...
```

The target is the model itself. The judge sees the final response text.

## Hosted model with Python tools

```yaml
rollout:
  target:
    model:
      name: azure/gpt-5.4-mini
    system_prompt: |
      You are a travel-planning assistant. ...
    tools:
      module: examples.travel_planner_tools
```

`tools.module` points at a Python module that exposes tool functions. The pipeline injects their signatures into the model call, executes any tool-call responses, and feeds results back to the model for the next turn.

## Simulated tools

For prompt-agent demos where you don't have real tool backends, point at a simulator:

```yaml
rollout:
  target:
    model:
      name: azure/gpt-5.4-mini
    system_prompt: |
      You are a healthy-recipe assistant. ...
    tools:
      toolset: examples.pipes.health_assistant_simulated_tools:TOOLSET
      simulator: examples.pipes.health_assistant_simulated_tools:simulate
```

The simulator returns canned tool responses. This is useful for prompt + spec iteration before real tools exist. **Not a replacement** for tracing a real framework agent — once you have one, switch to `target.callable` + OTel.

## When to use what

| Your agent | Use |
|---|---|
| Hosted model, no tools | `target.model` |
| Hosted model + simple Python tools | `target.model` + `target.tools.module` |
| Hosted model + tools you don't have backends for | `target.model` + `target.tools.toolset` + `tools.simulator` |
| Anything with a framework | [`target.callable` + OTel](/adaptive-eval/run/callable/) |
