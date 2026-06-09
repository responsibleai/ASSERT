<h1 align="center">
        <img src="https://raw.githubusercontent.com/responsibleai/ASSERT/main/assets/assert-logo.png" alt="ASSERT logo" width="22" style="vertical-align: middle; margin-right: 5px;"/>
        <span style="vertical-align: middle; font-family: 'Spline Sans Mono', monospace;">ASSERT.</span>
</h1>
<p align="center">
        Adaptive Spec-driven Scoring for Evaluation and Regression Testing<br/>
        Local-first. Framework-agnostic. Trace-aware.
</p>
<p align="center">
        <a href="https://github.com/responsibleai/ASSERT/blob/main/docs/getting-started.md">🚀 Get started</a> |
        <a href="https://responsibleai.github.io/ASSERT/">🌐 Visit project website</a> |
        <a href="https://github.com/responsibleai/ASSERT/blob/main/docs/targets/callable.md">🔌 View supported targets</a> |
        <a href="https://github.com/responsibleai/ASSERT/blob/main/docs/cli/overview.md">📘 CLI Reference</a> |
        <a href="https://github.com/responsibleai/ASSERT/blob/main/examples/README.md">🧪 Examples</a>
</p>
<p align="center">
        <a href="https://github.com/responsibleai/ASSERT/actions/workflows/build.yml"><img src="https://github.com/responsibleai/ASSERT/actions/workflows/build.yml/badge.svg" alt="Build status"></a>
        <a href="https://www.python.org/downloads/" target="_blank"><img src="https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg" alt="Python 3.11 | 3.12 | 3.13"></a>
        <a href="https://github.com/responsibleai/ASSERT/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License"></a>
</p>
<p align="center">
        <img src="https://raw.githubusercontent.com/responsibleai/ASSERT/main/assets/assert-ai-framework-diagram.png" alt="Diagram of the ASSERT evaluation framework" width="100%">
</p>

## Why ASSERT?

Most AI systems start with a specification: product requirements, policies, system prompts, or launch criteria describing what the system should and should not do.

But evaluation often starts elsewhere: generic scorers, predefined benchmarks, or manual test cases that drift from the original intent.

ASSERT closes that gap. It turns your specified behaviors in natural language into structured, executable evaluations that can be reviewed, run, scored, and improved over time.

From the natural language specification, the ASSERT pipeline derives behavior categories, generates single-turn and multi-turn test cases, inferences them against your target, and uses an LLM judge to score each conversation against your policies.

## What you get with ASSERT

- **Spec-driven coverage** - test cases are generated from your product requirements and context, not a generic benchmark. You specify the behaviors that you want to test for
- **Test any model endpoint** via integrations with [LiteLLM](https://github.com/BerriAI/litellm), supporting 100+ model endpoints from platform providers such as Bedrock, Azure, OpenAI, VertexAI, Cohere, Anthropic, Sagemaker, HuggingFace, VLLM, NVIDIA NIM.
- **Test any agent or multi-agent system** via integrations with [OpenInference](https://github.com/Arize-ai/openinference/). Evaluate a LangGraph agent, a CrewAI / OpenAI Agents SDK / DSPy / LlamaIndex / AutoGen system, custom multi-agent orchestration, a Python callable, or a hosted model — without rewriting the evaluation orchestration pipeline.
- **Agent trace-grounded judgment** - the recommended integration captures OpenTelemetry spans (OpenInference auto-instruments 33+ frameworks in two lines — `from assert_ai import auto_trace; auto_trace.enable()` — or you can emit your own with the OTel SDK) so the judge can cite tool calls, routing, model calls, and latency as evidence — not just the final response.
- **Portable artifacts** - every stage writes JSON/JSONL files locally for inspection, CI, and sharing.
- **Bundled local viewer** - browse runs side-by-side, pin a baseline, drill into per-behavior dimension breakdowns, and read judge justifications cited against the captured traces.

## Get started

```bash
pip install assert-ai
```

Then pick the path that matches what you already have:

**I have an agent** (LangGraph, CrewAI, OpenAI Agents SDK, custom…) — connect it and capture traces so the judge scores tool calls and routing, not just the final text:

```bash
pip install "assert-ai[otel]"          # quote the brackets so your shell keeps them
```

```python
# eval_target.py
from assert_ai import auto_trace
auto_trace.enable()                    # 2 lines: judge sees tool calls + routing
from my_app import run_agent           # your agent's entry function
```

```yaml
# eval_config.yaml — point the eval at your agent
pipeline:
  inference:
    target:
      callable: eval_target:run_agent
      trace:
        backend: phoenix
```

```bash
assert-ai run --config eval_config.yaml
```

**I have the agent spec** (a system prompt or description) — run a Prompt Agent eval on a base install:

```bash
pip install assert-ai
assert-ai run --example health-assistant
```

**Help me start** — describe your system and let an LLM assistant design the eval:

```bash
pip install assert-ai
assert-ai init --describe "a customer-support bot for an online bank"
assert-ai run --config eval_config.yaml
```

See the [full quickstart](docs/getting-started.md) for trace setup, reading results, and developing from source.

<table align="center" style="width: 100%; border: 1px solid #d0d7de; border-collapse: collapse;">
        <tr>
                <th style="border: 1px solid #d0d7de; padding: 10px; text-align: left;">🌐 Project website ↗</th>
                <th style="border: 1px solid #d0d7de; padding: 10px; text-align: left;">📝 Technical blog ↗</th>
                <th style="border: 1px solid #d0d7de; padding: 10px; text-align: left;">🚀 Quickstart guide ↗</th>
                <th style="border: 1px solid #d0d7de; padding: 10px; text-align: left;">📚 Documentation ↗</th>
        </tr>
        <tr>
                <td style="border: 1px solid #d0d7de; padding: 10px;"><a href="https://aka.ms/assert-ghpage">Learn about ASSERT</a></td>
                <td style="border: 1px solid #d0d7de; padding: 10px;"><a href="https://aka.ms/assert">Read the Command Line post</a></td>
                <td style="border: 1px solid #d0d7de; padding: 10px;"><a href="https://github.com/responsibleai/ASSERT/blob/main/docs/getting-started.md">Follow the full walkthrough</a></td>
                <td style="border: 1px solid #d0d7de; padding: 10px;"><a href="https://aka.ms/assert-docs">Browse concepts and guides</a></td>
        </tr>
</table>

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft trademarks or logos is subject to and must follow [Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks). Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship. Any use of third-party trademarks or logos is subject to those third party's policies.

## Telemetry

This project does not collect or send telemetry to Microsoft by default. Runs write local artifacts under `artifacts/results/`, and optional OpenTelemetry trace capture is controlled by your configuration and local collector setup, such as Phoenix.

If you configure a target, judge, trace collector, or model provider to send data to an external service, the prompts, responses, traces, metadata, and other evaluation artifacts sent to that service are governed by that service's terms and your configuration.

## Disclaimer: Risks and limitations of ASSERT

See the full section in the [`Concept Doc`](https://github.com/responsibleai/ASSERT/blob/main/docs/concepts.md#risks-and-limitations).
