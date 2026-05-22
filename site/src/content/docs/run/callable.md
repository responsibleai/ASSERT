---
title: Callable target
description: Wrap any Python entrypoint as an Adaptive Eval target.
---

`target.callable` accepts any Python function or class method you can import. For any agent or multi-agent system beyond a single-shot tool-calling model, this is the recommended integration.

## Minimal example

Your agent is a function that takes a string and returns a string:

```python
# my_agent.py
def chat(message: str) -> str:
    return run_my_agent(message)
```

Point the config at it:

```yaml
inference:
  target:
    callable: my_agent:chat
```

## With OpenTelemetry auto-trace (recommended)

Wrap your agent's entry function with a Phoenix instrumentation registration:

```python
# auto_trace.py
from phoenix.otel import register

register(auto_instrument=True)

from my_agent import chat as chat_sync   # re-export after registration
```

Then point the config at the wrapper:

```yaml
inference:
  target:
    callable: auto_trace:chat_sync
    trace:
      backend: phoenix
      group_by: session.id
```

OpenInference auto-instruments the framework underneath your agent (LangGraph, CrewAI, OpenAI Agents SDK, DSPy, LlamaIndex, AutoGen/MAF, ...). Every tool call, every routing decision, every internal LLM call flows into the OTel collector and is linked into the transcript by `session.id`.

## Returning a `ModelResponse`

If you have custom orchestration and want to surface tool calls without going through OTel, return a `ModelResponse` instead of a string:

```python
from p2m.core.model_client import ModelResponse, ToolCall

def chat(message: str) -> ModelResponse:
    result = my_orchestration(message)
    return ModelResponse(
        text=result["final_text"],
        model="gpt-5.4-mini",
        tool_calls=[ToolCall(name=tc["name"], arguments=tc["args"]) for tc in result["tool_calls"]],
    )
```

This is a middle ground: more visibility than `str`, less than OTel.

## Async callables

Async functions are supported transparently:

```python
async def chat(message: str) -> str:
    return await my_async_agent(message)
```
