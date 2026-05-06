# Callable Agent Target (with optional trace capture)

> **Old name:** "OTel Agent Target". This path covers any agent or multi-agent system with a Python entry function. OpenTelemetry trace capture is an optional upgrade — not a prerequisite.

Use this target when you have a real agent (single-agent or multi-agent) and want Adaptive Eval to inspect both final answers and — optionally — the execution trajectory.

## When to use it

Use this path for any agent or multi-agent system you can call from Python, including:

- Framework agents: LangGraph, LangChain, OpenAI Agents SDK, CrewAI, LlamaIndex, AutoGen / Microsoft Agent Framework, DSPy
- Custom orchestration or bespoke multi-agent systems with no framework
- Plain Python functions that wrap a hosted model

> If you only have a hosted model with a system prompt, the [model + tools target](model-and-tools.md) is simpler.

## How it works (final-text only — minimum viable path)

The minimum integration is a Python callable that takes a user message and returns a string:

```python
def chat_sync(message: str) -> str:
    # call your agent or multi-agent graph here
    return final_response
```

```yaml
pipeline:
  inference:
    target:
      callable: package.module:chat_sync
    tester:
      model: { name: azure/gpt-5.4-mini, temperature: 0.0 }
    max_turns: 6
```

That is enough to run the full pipeline and judge final answers. No OpenTelemetry required.

## Optional: add trace capture for richer judge evidence

When the judge would benefit from seeing tool calls, routing, or intermediate decisions, add Phoenix/OpenInference auto-instrumentation to your wrapper and set `target.trace`:

```python
from phoenix.otel import register

register(auto_instrument=True)

from examples.travel_planner_langgraph.agent import chat_sync
```

```yaml
pipeline:
  inference:
    target:
      callable: examples.travel_planner_langgraph.auto_trace:chat_sync
      trace:
        backend: phoenix
        group_by: session.id
    tester:
      model: { name: azure/gpt-5.4-mini, temperature: 0.0 }
    max_turns: 6
```

Trace fidelity depends on what your framework and instrumentation emit. The best-supported paths capture model calls, tool calls, arguments, routing, latency, and intermediate messages.

## Why trace capture matters for multi-agent systems

For multi-agent and tool-using systems, final text is often insufficient. Trace capture helps answer:

- Which tool did the agent call?
- What arguments did it send?
- Did it skip a required safety or validation step?
- Did it route to the wrong sub-agent?
- Did a tool return data that the final answer ignored or fabricated around?

## Flagship example

Run:

```powershell
uv run p2m run --config examples\travel_planner_langgraph\eval_config.yaml
```

Then inspect:

- `transcripts.jsonl`
- `scores.jsonl`
- Phoenix traces, if Phoenix is running

## Caveat

`target.callable` is the integration boundary. If your callable does not accept conversation history, each target invocation is a fresh call from the agent's perspective while Adaptive Eval maintains the outer transcript.
