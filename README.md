<h1 align="center">
        <img src="assets/assert-logo.png" alt="ASSERT logo" width="22" style="vertical-align: middle; margin-right: 5px;"/>
        <span style="vertical-align: middle; font-family: 'Spline Sans Mono', monospace;">ASSERT.</span>
</h1>
<p align="center">
        Adaptive Spec-driven Scoring for Evaluation and Regression Testing<br/>
        Local-first. Framework-agnostic. Trace-aware.
</p>
<p align="center">
        <a href="docs/getting-started.md">🚀 Get started</a> |
        <a href="docs/targets/callable.md">🔌 View supported targets</a> |
        <a href="docs/cli/overview.md">📘 CLI Reference</a> |
        <a href="examples/README.md">🧪 Examples</a>
</p>
<p align="center">
        <a href="LICENSE">
                <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License: MIT">
        </a>
        <a href="https://www.python.org/downloads/" target="_blank">
                <img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11+">
        </a>
</p>
<p align="center">
        <img src="assets/assert-ai-framework-diagram.png" alt="Diagram of the ASSERT evaluation framework" width="100%">
</p>

> [!IMPORTANT]
> **Migration note (May 2026):** The Python package was renamed `assert_eval` → `assert_ai`,
> the CLI was renamed `assert-eval` → `assert-ai`, and environment variables were renamed
> `ASSERT_EVAL_*` → `ASSERT_AI_*` (the pre-rename `P2M_*` aliases are also gone). If you
> installed an earlier preview, update your imports, CLI invocations, and `.env` files. See
> [CHANGELOG.md](./CHANGELOG.md) for details.

## Why ASSERT?

Most AI systems start with a specification: product requirements, policies, system prompts, or launch criteria describing what the system should and should not do.

But evaluation often starts elsewhere: generic scorers, predefined benchmarks, or manual test cases that drift from the original intent.

ASSERT closes that gap. It turns your specified behaviors in natural language into structured, executable evaluations that can be reviewed, run, scored, and improved over time.

From the natural language specification, the ASSERT pipeline derives behavior categories, generates single-turn and multi-turn test cases, inferences them against your target, and uses an LLM judge to score each conversation against your policies.

## What you get with ASSERT

- **Spec-driven coverage** - test cases are generated from your product requirements and context, not a generic benchmark. You specify the behaviors that you want to test for
- **Test any model endpoint** via integrations with [LiteLLM](https://github.com/BerriAI/litellm), supporting 100+ model endpoints from platform providers such as Bedrock, Azure, OpenAI, VertexAI, Cohere, Anthropic, Sagemaker, HuggingFace, VLLM, NVIDIA NIM.
- **Test any agent or multi-agent system** via integrations with [OpenInference](https://github.com/Arize-ai/openinference/). Evaluate a LangGraph agent, a CrewAI / OpenAI Agents SDK / DSPy / LlamaIndex / AutoGen system, custom multi-agent orchestration, a Python callable, or a hosted model — without rewriting the evaluation orchestration pipeline.
- **Agent trace-grounded judgment** - the recommended integration captures OpenTelemetry spans (Phoenix/OpenInference auto-instruments 33+ frameworks in two lines, or you can emit your own with the OTel SDK) so the judge can cite tool calls, routing, model calls, and latency as evidence — not just the final response.
- **Portable artifacts** - every stage writes JSON/JSONL files locally for inspection, CI, and sharing.

## Get started

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
                <td style="border: 1px solid #d0d7de; padding: 10px;"><a href="docs/getting-started.md">Follow the full walkthrough</a></td>
                <td style="border: 1px solid #d0d7de; padding: 10px;"><a href="https://aka.ms/assert-docs">Browse concepts and guides</a></td>
        </tr>
</table>

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft trademarks or logos is subject to and must follow [Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks). Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship. Any use of third-party trademarks or logos is subject to those third party's policies.

## Telemetry

This project does not collect or send telemetry to Microsoft by default. Runs write local artifacts under `artifacts/results/`, and optional OpenTelemetry trace capture is controlled by your configuration and local collector setup, such as Phoenix.

If you configure a target, judge, trace collector, or model provider to send data to an external service, the prompts, responses, traces, metadata, and other evaluation artifacts sent to that service are governed by that service's terms and your configuration.

## Disclaimer: Risks and limitations of ASSERT

See the full section in the [`Concept Doc`](docs/concepts.md#risks-and-limitations).
