# Target Support Overview

ASSERT can evaluate any agent or multi-agent system. Pick the path that matches how your agent is built.

## Who owns the tool-call loop?

Two questions decide which target you want:

1. **Have you written orchestration code** — tool routing, sub-agents, multi-step planning? If yes, use the callable target. Your code owns the loop; ASSERT calls your function and (recommended) reads the OTel spans you emit.
2. **Or do you just have a system prompt and a tool schema** and want ASSERT to run the loop for you? That is the **Prompt Agent target**. You declare in YAML; the runtime orchestrates (up to 10 tool-call rounds, real Python tools or LLM-simulated tool responses).

The third path — plain callable without traces — is a customization fallback for black-box APIs you cannot instrument.

## Recommended path: callable target with OTel traces

For any agent or multi-agent system you can invoke from Python — LangGraph, CrewAI, OpenAI Agents SDK, DSPy, LlamaIndex, AutoGen / MAF, custom orchestration, or any other framework — use the **callable target with OpenTelemetry trace capture**. This is the universal integration boundary, and the OTel spans give the judge the tool calls, routing, and intermediate decisions it needs to score real behavior.

For 33+ supported frameworks the instrumentation is two lines:

```python
from phoenix.otel import register
register(auto_instrument=True)
```

For unsupported frameworks or custom orchestration, emit your own OTel spans with the OpenTelemetry SDK; `target.trace` reads the same span data either way.

→ See [`callable.md`](callable.md).

## Alternate path: Prompt Agent (model + tools)

Use the **Prompt Agent target** (`target.model` + `target.system_prompt` + optional `target.tools`) when you have a system prompt and a tool schema but no orchestration code yet. The runtime owns the tool-call loop. Real Python tools or LLM-simulated tool responses both work. Useful for test-driven prompt + toolset design *before* any agent is implemented.

→ See [`model-and-tools.md`](model-and-tools.md).

## Customization: plain callable without traces

The callable target also accepts a plain Python function with no `target.trace` block. **This is not recommended for real agents** — the judge sees only the final response and misses tool calls, routing, and intermediate decisions. Use it only as a fallback when you cannot instrument the target (for example, evaluating a black-box third-party API), or for pipeline smoke testing.

## Target paths at a glance

| Path | Who owns the tool-call loop? | Best for | Config anchor |
|---|---|---|---|
| Callable target with OTel traces (recommended) | You (your callable runs the loop; ASSERT reads the OTel spans) | Any agent or multi-agent system you can invoke from Python | `target.callable` + `target.trace` |
| Prompt Agent (model + tools) | ASSERT runtime (declared in YAML; runtime orchestrates up to 10 rounds) | Test-driven prompt + toolset design; agents that haven't been written yet | `target.model`, `target.system_prompt`, `target.tools` |
| Plain callable (customization fallback) | Whoever (ASSERT doesn't see inside) | Black-box APIs you cannot instrument; pipeline smoke tests | `target.callable` (no `target.trace`) |

## What is not a preview-first path

The customer preview docs do not lead with an external connector path. For most agents, the OTel-traced callable target is simpler, easier to debug, and closer to how developers already run local code.
