# Getting Started

This guide covers installation and your first end-to-end evaluation run.

## Prerequisites

- Python 3.11+
- A model key in your environment or `.env` (for example `AZURE_API_KEY` and `AZURE_API_BASE` for Azure OpenAI). ASSERT routes to 100+ providers through LiteLLM.

## Install

```bash
pip install assert-ai
```

That is the whole tool. The paths below add an extra only when they need one (for example `"assert-ai[otel]"` for trace capture) — always quote the brackets so your shell doesn't expand them.

## Quickstart

Pick the path that matches what you already have.

<!--quickstart-tabs-->
### I have an agent

Connect an agent you already built (LangGraph powers ~half of agent builds; CrewAI, OpenAI Agents SDK, LlamaIndex, AutoGen, or a custom loop work the same way). Two lines of trace capture let the judge score tool calls and routing — not just the final text.

```bash
pip install "assert-ai[otel]"
# set your model key: export AZURE_API_KEY and AZURE_API_BASE (or create a .env here — it's auto-loaded)
```

Wrap your agent's entry function so its spans are captured:

```python
# eval_target.py
from assert_ai import auto_trace
auto_trace.enable()                 # the 2 lines: judge sees tool calls + routing

from my_app import run_agent        # your existing agent entry function
```

Point the eval at it:

```yaml
# eval_config.yaml
pipeline:
  inference:
    target:
      callable: eval_target:run_agent   # module:function
      trace:
        backend: phoenix
```

```bash
assert-ai run --config eval_config.yaml
```

Prefer to watch a worked example run first?

```bash
pip install "assert-ai[langgraph,otel]"
assert-ai run --example travel-planner-langgraph
```

### I have the agent spec

You have a system prompt or a written description of how the agent should behave, but no code to wire up yet. ASSERT evaluates the spec directly as a Prompt Agent — it runs on a base install.

```bash
pip install assert-ai
# set your model key: export AZURE_API_KEY and AZURE_API_BASE (or create a .env here — it's auto-loaded)
assert-ai run --example health-assistant
```

Swap in your own spec by editing `target.system_prompt` and `behavior.description` in the generated config.

### Help me start

No spec yet? Describe your system in one line and an LLM assistant interviews you, then writes a complete `eval_config.yaml`.

```bash
pip install assert-ai
# set your model key: export AZURE_API_KEY and AZURE_API_BASE (or create a .env here — it's auto-loaded)
assert-ai init --describe "a customer-support bot for an online bank"
assert-ai run --config eval_config.yaml
```
<!--/quickstart-tabs-->

## Run and inspect results

Each run writes portable artifacts under `artifacts/results/<suite>/<run>/`. Check status with:

```bash
assert-ai results status health-assistant-v1 demo-1
```

`phoenix serve` is optional — run it only if you want a browser UI to inspect captured traces on http://localhost:6006. The eval and the judge see the same span data either way.

<details>
<summary><strong>Develop from source</strong> (contributing, or running the repo examples directly)</summary>

Clone the repo and install editable with the extras you need. The bundled `--example` configs above cover the common cases without a clone.

macOS / Linux:

```bash
git clone https://github.com/responsibleai/ASSERT && cd ASSERT
python -m venv .venv && source .venv/bin/activate
pip install -e ".[otel,langgraph]"
cp .env.example .env
assert-ai run --config examples/travel_planner_langgraph/eval_config.yaml
```

Windows (PowerShell):

```powershell
git clone https://github.com/responsibleai/ASSERT; cd ASSERT
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e ".[otel,langgraph]"
Copy-Item .env.example .env
assert-ai run --config examples/travel_planner_langgraph/eval_config.yaml
```

A minimal dev container is also included: [![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/responsibleai/ASSERT)

</details>

## What just happened

1. `systematize` expanded the behavior spec into behavior categories.
2. `test_set` generated prompt and scenario test cases.
3. `inference` executed the target for each case.
4. `judge` produced verdicts, evidence, and aggregate metrics.

What the quickstart does:

| Step | Developer behavior | Current YAML / artifact |
|---|---|---|
| 1 | **Eval spec**: plain-English behavior requirements | `behavior.name` and `behavior.description` live inline in `eval_config.yaml` |
| 2 | **Behavior categories**: generated failure-mode taxonomy | `pipeline.systematize` writes `taxonomy.json` |
| 3 | **Test cases**: prompts and multi-turn scenarios | `pipeline.test_set` writes `test_set.jsonl` |
| 4 | **Execute**: run the agent and capture traces | `pipeline.inference.target.callable` + `target.trace` write `inference_set.jsonl` |
| 5 | **Judge**: score against your rubric | `pipeline.judge.dimensions` writes `scores.jsonl` and `metrics.json` |

### CLI helper assistant to create your own config

Don't want to write YAML by hand? `assert-ai init` starts a conversational LLM assistant that asks about your agent, eval goals, and constraints, then proposes a complete config YAML file to use for your evaluations.

`assert-ai init` needs an LLM to power the conversation. Pass `--model` with any [LiteLLM model string](https://docs.litellm.ai/docs/providers) and make sure the matching API key is set in your `.env` file (loaded by default) or environment:

```bash
assert-ai init --model azure/gpt-5.4
# or skip the first question:
assert-ai init --model azure/gpt-5.4 --describe "A customer-support chatbot with order-lookup and refund tools"
# or edit/extend an existing config:
assert-ai init --model azure/gpt-5.4 --from examples/travel_planner_langgraph/eval_config.yaml
```

See [CLI Commands](cli/commands.md) for the full option reference.

- To learn the config format, see [Config Overview](config/overview.md).
- To inspect outputs in detail, see [Results Guide](guides/results.md).
- To use the local web viewer, see [Run the Local UI Viewer Application](guides/run-local-viewer.md).
