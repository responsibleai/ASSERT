# Callable Target

Use the callable target for any agent or multi-agent system with a Python entry function. This is the universal integration boundary — frameworks (LangGraph, CrewAI, OpenAI Agents SDK, DSPy, LlamaIndex, AutoGen / MAF, …), custom orchestration, REST clients, and thin model wrappers all qualify.

The callable target has two integration paths. **Pick the OTel-traced path for any non-trivial agent.** The plain-callable path exists as a customization fallback and is not recommended for evaluating real agents.

## Recommended path: OTel-traced agent

When your agent emits OpenTelemetry spans, the judge can cite tool arguments, routing decisions, model calls, and latency as evidence — not just the final response. This is the integration shape every flagship example uses.

For 33+ supported frameworks (OpenAI Agents SDK, LangChain/LangGraph, CrewAI, DSPy, LlamaIndex, AutoGen, MAF, Pydantic AI, Smolagents, Instructor, Haystack, …), instrumentation is **two lines** at the top of your callable module:

```python
# e.g. examples/travel_planner_langgraph/auto_trace.py
from phoenix.otel import register

register(auto_instrument=True)  # picks up any installed openinference-instrumentation-* package

def chat_sync(message: str, history: list[dict[str, str]] | None = None) -> str:
    return run_my_agent(message, history)
```

Wire the target up in your config:

```yaml
pipeline:
  rollout:
    target:
      callable: examples.travel_planner_langgraph.auto_trace:chat_sync
      trace:
        backend: phoenix
        group_by: session.id
```

For frameworks not on the auto-instrument list, or for custom orchestration, emit your own OTel spans with the OpenTelemetry SDK and the same `target.trace` config picks them up. See `examples/travel_planner_neurosan/` for a manual-span example.

### Why traces matter to the judge

The judge can only score what it sees. With final text only:

- it cannot tell if the agent used the right tool with the right arguments
- it cannot tell which sub-agent or branch made a decision
- "the answer was right but for the wrong reason" looks like a pass

With trace capture, the judge cites specific spans as evidence and catches process failures even when the surface answer looks fine.

## Callable shape

The callable can be synchronous or asynchronous. The simplest shape:

```python
def chat_sync(message: str) -> str:
    return "assistant response"
```

If your callable accepts a `history` parameter, Adaptive Eval passes prior user/assistant turns:

```python
def chat_sync(message: str, history: list[dict[str, str]]) -> str:
    ...
```

Use this shape for agents that need multi-turn state during scenario rollout.

The callable can return:

- a plain string
- a structured model response supported by the runtime
- a dictionary with text/content fields

## Customization path: plain callable without traces

You can omit the `target.trace` block, but only when:

- your target is a black-box API you cannot instrument (e.g. a third-party endpoint with no execution surface to trace)
- you are running a quick smoke against a thin model wrapper with no real internals
- you are validating the eval pipeline itself, not the agent

**This path is not recommended for evaluating real agents or multi-agent systems.** Without traces, the judge sees only the final response and misses tool calls, routing, and intermediate decisions. Add OTel instrumentation as soon as the agent has internals worth scoring.
