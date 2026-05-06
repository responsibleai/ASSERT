# Callable Target

Use the callable target for any agent or multi-agent system with a Python entry function. This is the universal integration boundary — frameworks (LangGraph, CrewAI, OpenAI Agents SDK, DSPy, LlamaIndex, AutoGen / MAF, …), custom orchestration, REST clients, and thin model wrappers all qualify.

## Shape

The callable can be synchronous or asynchronous. The simplest shape is:

```python
def chat_sync(message: str) -> str:
    return "assistant response"
```

Then configure:

```yaml
pipeline:
  inference:
    target:
      callable: package.module:chat_sync
```

## Conversation history

If your callable accepts a `history` parameter, Adaptive Eval can pass prior user/assistant turns:

```python
def chat_sync(message: str, history: list[dict[str, str]]) -> str:
    ...
```

Use this shape for agents that need multi-turn state during scenario inference.

## Return values

The callable can return:

- a plain string
- a structured model response supported by the runtime
- a dictionary with text/content fields

## Optional: add trace capture for richer evidence

When the judge would benefit from seeing tool calls, routing, or intermediate decisions, add OpenTelemetry instrumentation around your callable. See [`otel-agent.md`](otel-agent.md) for the optional `target.trace` upgrade.

## When the plain callable is enough

Use the plain callable (no trace capture) when:

- you want a quick first integration
- final text is enough for the first eval
- the target does not yet emit useful spans
- you are evaluating a small wrapper around an existing system

Add trace capture later when tool calls, routing, or intermediate decisions matter to the judge.
