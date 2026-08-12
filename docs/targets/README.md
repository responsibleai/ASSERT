# Target Support Overview

ASSERT can evaluate any hosted model, agent or multi-agent system. Pick the path that matches how your AI system is built.

## Choose your target

Pick a target based on how your agent is built.

| Your target looks like... | Use this path | Start here |
|---|---|---|
| A system prompt + tool schema, no orchestration code yet | **Prompt Agent target** (`target.model`, `target.system_prompt`, `target.tools`): the runtime owns the tool-call loop (up to 10 rounds, real or simulated tools). Best for test-driven prompt + toolset design before any agent is implemented | [Prompt Agent Target (model + tools)](model-and-tools.md) |
| Any agent or multi-agent system you can invoke from Python (LangGraph, CrewAI, OpenAI Agents SDK, DSPy, LlamaIndex, AutoGen / MAF, custom orchestration, and others) | **Callable target with OTel traces (recommended)**: point `target.callable` at your entry function and add `target.trace` so Phoenix/OpenInference (or your own OTel SDK spans) feed tool calls, routing, model calls, and latency to the judge | [Callable Target](callable.md) |
| Existing OpenTelemetry traces from a prior run | **Judge pre-collected traces**: parse the trace file into an inference set with `assert-ai judge-traces --traces <path> --config <path>`, then score it with `assert-ai run --config <path> --force-stage judge` | [CLI reference](../cli/commands.md#judge-traces) |
| A black-box HTTP service you cannot import as Python | **HTTP endpoint target**: point `target.endpoint` at the service URL. The runtime POSTs to it directly — no wrapper code. Same black-box visibility as a plain callable: the judge sees only the final response | [HTTP endpoint (`target.endpoint`)](callable.md#http-endpoint-targetendpoint) |

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

## Offline path: bring your own OTel traces

If your repo already emits OpenTelemetry spans, you can turn a captured trace file into scored
results without running live inference — in two steps:

```bash
assert-ai judge-traces --traces <path> --config <path>
assert-ai run --config <path> --force-stage judge
```

`judge-traces` parses the OTel spans into an inference set (`inference_set.jsonl`); it does not
call the judge itself. `--force-stage judge` runs the judge stage against that inference set and
produces `scores.jsonl`. This is separate from `assert-ai run`'s normal path: there is no
`--trace` flag on `assert-ai run`. Use `target.callable` + `target.trace` when ASSERT should run
the target and collect traces; use `judge-traces` + `--force-stage judge` when traces already
exist.

## Simple path: Prompt Agent (model + tools)

Use the **Prompt Agent target** (`target.model` + `target.system_prompt` + optional `target.tools`) when you have a system prompt and a tool schema but no orchestration code yet. The runtime owns the tool-call loop. Real Python tools or LLM-simulated tool responses both work. Useful for test-driven prompt + toolset design *before* any agent is implemented.

→ See [Prompt Agent Target](model-and-tools.md).

## Customization: plain callable without traces

The callable target also accepts a plain Python function with no `target.trace` block. **This is not recommended for real agents** — the judge sees only the final response and misses tool calls, routing, and intermediate decisions. Use it only as a fallback when you cannot instrument the target (for example, evaluating a black-box third-party API), or for pipeline smoke testing.

ASSERT evaluates HTTP services natively — see [HTTP endpoint (`target.endpoint`)](callable.md#http-endpoint-targetendpoint). Reach for a callable shim only when your service's request or response shape differs from the one `target.endpoint` expects (it POSTs `{"message": ..., "history": [...]}` and reads `{"response": ...}`):

```python
import requests


def call_agent(message: str) -> str:
    """Adapter for a service whose contract differs from target.endpoint's."""
    response = requests.post(
        "https://example.com/agent",
        json={"input": message},          # this service wants "input", not "message"
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["output"]      # ...and returns "output", not "response"
```

Because this path has no trace capture, the judge sees only the returned text. Prefer a traced Python callable whenever you control the agent runtime.

## Target paths at a glance

| Path | Who owns the tool-call loop? | Best for | Config anchor |
|---|---|---|---|
| Callable target with OTel traces (recommended) | You (your callable runs the loop; ASSERT reads the OTel spans) | Any agent or multi-agent system you can invoke from Python | `target.callable` + `target.trace` |
| Pre-collected OTel traces | You (`judge-traces` parses spans into an inference set; `--force-stage judge` scores them) | Repos that already captured spans from a prior run | `assert-ai judge-traces --traces <path> --config <path>` then `assert-ai run --config <path> --force-stage judge` |
| Prompt Agent (model + tools) | ASSERT runtime (declared in YAML; runtime orchestrates up to 10 rounds) | Test-driven prompt + toolset design; agents that haven't been written yet | `target.model`, `target.system_prompt`, `target.tools` |
| HTTP endpoint | The HTTP service (ASSERT doesn't see inside) | A deployed service you cannot import as Python | `target.endpoint` |
| Plain callable (customization fallback) | Whoever (ASSERT doesn't see inside) | Uninstrumentable targets; services whose HTTP contract differs from `target.endpoint`'s; pipeline smoke tests | `target.callable` (no `target.trace`) |

## Current support

The current documentation does not lead with an external connector path. For most agents, the OTel-traced callable target is simpler, easier to debug, and closer to how developers already run local code.
