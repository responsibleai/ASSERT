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
  rollout:
    target:
      callable: package.module:chat_sync
```

## Conversation history

If your callable accepts a `history` parameter, Adaptive Eval can pass prior user/assistant turns:

```python
def chat_sync(message: str, history: list[dict[str, str]]) -> str:
    ...
```

Use this shape for agents that need multi-turn state during scenario rollout.

## Return values

The callable can return:

- a plain string
- a structured model response supported by the runtime
- a dictionary with text/content fields

## Optional: add trace capture for richer evidence

When the judge would benefit from seeing tool calls, routing, or intermediate decisions, add OpenTelemetry instrumentation around your callable. The simplest path is Phoenix + OpenInference auto-instrumentation:

```python
# in your callable module, e.g. examples/travel_planner_langgraph/auto_trace.py
from phoenix.otel import register

register(auto_instrument=True)  # picks up any OpenInference instrumentor on PYTHONPATH

def chat_sync(message: str, history: list[dict[str, str]] | None = None) -> str:
    return run_my_agent(message, history)
```

Then opt in from your config:

```yaml
pipeline:
  rollout:
    target:
      callable: examples.travel_planner_langgraph.auto_trace:chat_sync
      trace:
        backend: phoenix
        group_by: session.id
```

Adaptive Eval will capture the OTel spans your agent emits and attach them to each transcript so the judge can cite tool arguments, routing decisions, and latency — not just the final response.

### Why trace capture matters

The judge can only score what it sees. With final-text-only:

- it cannot tell if the agent used the right tool with the right arguments
- it cannot tell which sub-agent or branch made a decision
- "the answer was right but for the wrong reason" looks like a pass

With trace capture, the judge can cite specific spans as evidence and catch process failures even when the surface answer looks fine.

## When the plain callable is enough

Use the plain callable (no trace capture) when:

- you want a quick first integration
- final text is enough for the first eval
- the target does not yet emit useful spans
- you are evaluating a small wrapper around an existing system

Add trace capture later when tool calls, routing, or intermediate decisions matter to the judge.
