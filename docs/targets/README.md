# Target Support Overview

ASSERT can evaluate any hosted model, agent or multi-agent system. Pick the path that matches how your AI system is built.

## Choose your target

Pick a target based on how your agent is built.

| Your target looks like... | Use this path | Start here |
|---|---|---|
| A system prompt + tool schema, no orchestration code yet | **Prompt Agent target** (`target.model`, `target.system_prompt`, `target.tools`): the runtime owns the tool-call loop (up to 10 rounds, real or simulated tools). Best for test-driven prompt + toolset design before any agent is implemented | [Prompt Agent Target (model + tools)](model-and-tools.md) |
| Any agent or multi-agent system you can invoke from Python (LangGraph, CrewAI, OpenAI Agents SDK, DSPy, LlamaIndex, AutoGen / MAF, custom orchestration, and others) | **Callable target with OTel traces (recommended)**: point `target.callable` at your entry function and add `target.trace` so Phoenix/OpenInference (or your own OTel SDK spans) feed tool calls, routing, model calls, and latency to the judge | [Callable Target](callable.md) |
| A black-box API or already-running OpenAI-compatible service | **Endpoint target** (`target.endpoint`): call a service-hosted target over HTTP. Use `protocol: openai_chat` for Chat Completions-compatible servers, or the simple ASSERT endpoint protocol for custom adapters. Tool calls are captured only when the endpoint returns them. | [Endpoint Target](endpoint.md) |

**Use simulated tools intentionally:** simulated tools are helpful for Prompt Agents when real backends are not ready. They are not a substitute for tracing a real multi-agent framework.

## Recommended path: callable target with OTel traces

For any agent or multi-agent system you can invoke from Python — LangGraph, CrewAI, OpenAI Agents SDK, DSPy, LlamaIndex, AutoGen / MAF, custom orchestration, or any other framework — use the **callable target with OpenTelemetry trace capture**. This is the universal integration boundary, and the OTel spans give the judge the tool calls, routing, and intermediate decisions it needs to score real behavior.

For 33+ supported frameworks the instrumentation is two lines:

```python
from assert_ai import auto_trace
auto_trace.enable()
```

For unsupported frameworks or custom orchestration, emit your own OTel spans with the OpenTelemetry SDK; `target.trace` reads the same span data either way.

→ See [Callable Target](callable.md).

After an eval finds policy violations, see [Securing agents with ACS](../guides/securing-agents-with-acs.md) to generate an ACS guard and re-run the same callable target secured.

## Simple path: Prompt Agent (model + tools)

Use the **Prompt Agent target** (`target.model` + `target.system_prompt` + optional `target.tools`) when you have a system prompt and a tool schema but no orchestration code yet. The runtime owns the tool-call loop. Real Python tools or LLM-simulated tool responses both work. Useful for test-driven prompt + toolset design *before* any agent is implemented.

→ See [Prompt Agent Target](model-and-tools.md).

## Customization: endpoint and plain callable fallbacks

Use the endpoint target when your system is already running as a service or exposes an OpenAI-compatible Chat Completions endpoint. Endpoint targets are easier to connect to existing services, but ASSERT only sees final text unless the endpoint returns tool calls or `events` evidence.

The callable target also accepts a plain Python function with no `target.trace` block. **This is not recommended for real agents** — the judge sees only the final response and misses tool calls, routing, and intermediate decisions. Use it only as a fallback when you cannot instrument the target, or for pipeline smoke testing.

## Target paths at a glance

| Path | Who owns the tool-call loop? | Best for | Config anchor |
|---|---|---|---|
| Callable target with OTel traces (recommended) | You (your callable runs the loop; ASSERT reads the OTel spans) | Any agent or multi-agent system you can invoke from Python | `target.callable` + `target.trace` |
| Prompt Agent (model + tools) | ASSERT runtime (declared in YAML; runtime orchestrates up to 10 rounds) | Test-driven prompt + toolset design; agents that haven't been written yet | `target.model`, `target.system_prompt`, `target.tools` |
| Plain callable (customization fallback) | Whoever (ASSERT doesn't see inside) | Black-box APIs you cannot instrument; pipeline smoke tests | `target.callable` (no `target.trace`) |
| Endpoint target | Target service | Service-hosted targets and OpenAI-compatible chat endpoints | `target.endpoint` |

## Current support

The current documentation does not lead with an external connector path. For most agents, the OTel-traced callable target is simpler, easier to debug, and closer to how developers already run local code.
