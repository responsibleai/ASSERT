# Target Support Overview

Adaptive Eval can evaluate any agent or multi-agent system. Pick the path that matches how your agent is built.

## Recommended path: callable target with OTel traces

For any agent or multi-agent system you can invoke from Python — LangGraph, CrewAI, OpenAI Agents SDK, DSPy, LlamaIndex, AutoGen / MAF, custom orchestration, or any other framework — use the **callable target with OpenTelemetry trace capture**. This is the universal integration boundary, and the OTel spans give the judge the tool calls, routing, and intermediate decisions it needs to score real behavior.

For 33+ supported frameworks the instrumentation is two lines:

```python
from phoenix.otel import register
register(auto_instrument=True)
```

For unsupported frameworks or custom orchestration, emit your own OTel spans with the OpenTelemetry SDK; `target.trace` reads the same span data either way.

→ See [`callable.md`](callable.md).

## Alternate path: model + tools

Use `target.model`, `target.system_prompt`, and optional `target.tools` when your "agent" is just a hosted model with a system prompt — and possibly a small fixed tool set you want simulated by another model. Useful for quick prompt-agent smoke tests; not a substitute for tracing a real multi-agent framework.

→ See [`model-and-tools.md`](model-and-tools.md).

## Customization: plain callable without traces

The callable target also accepts a plain Python function with no `target.trace` block. **This is not recommended for real agents** — the judge sees only the final response and misses tool calls, routing, and intermediate decisions. Use it only as a fallback when you cannot instrument the target (for example, evaluating a black-box third-party API), or for pipeline smoke testing.

## Target paths at a glance

| Path | Best for | Config anchor |
|---|---|---|
| Callable target with OTel traces (recommended) | Any agent or multi-agent system you can invoke from Python | `target.callable` + `target.trace` |
| Model + tools | Hosted prompt agents or simple model/tool setups | `target.model`, `target.system_prompt`, `target.tools` |
| Plain callable (customization fallback) | Black-box APIs you cannot instrument; pipeline smoke tests | `target.callable` (no `target.trace`) |

## What is not a preview-first path

The customer preview docs do not lead with an external connector path. For most agents, the OTel-traced callable target is simpler, easier to debug, and closer to how developers already run local code.
