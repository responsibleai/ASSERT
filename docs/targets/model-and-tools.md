# Prompt Agent Target (model + tools)

A **Prompt Agent** is an agent declared in YAML — a hosted model + a system prompt + an optional tool schema — with no orchestration code. The runtime owns the tool-call loop (up to 10 rounds, real Python tools or LLM-simulated tool responses); you own the prompt and the schema.

The key value: **you can test the prompt and toolset design before any agent code is written.**

## Test-driven prompt + toolset design

You don't need an implemented agent to start evaluating. With a system prompt and a toolset YAML, you can run the full eval pipeline against a *simulated* tool layer — the runtime stands in a model that role-plays plausible tool responses. This lets you:

- iterate on the system prompt against realistic conversations before any orchestration is written
- catch toolset-design mistakes (missing arguments, ambiguous tool descriptions, unreachable tools) early
- decide which behavior_categories require real backends and which can be specced from the prompt + schema alone

```yaml
pipeline:
  inference:
    target:
      model:
        name: azure/gpt-4o-mini
        temperature: 0.0
      system_prompt: |
        You are a helpful assistant. Follow the product taxonomy and ask clarifying
        questions when user constraints are missing.
      tools:
        toolset: examples/agents/health_assistant_tools.yaml
        simulator: azure/gpt-4o-mini
```

The eval runs end-to-end: taxonomy → test cases → inference (with simulated tools) → judge verdicts on tool selection, argument correctness, and constraint handling. When the prompt and toolset look right, swap the simulator for real tool implementations (next section) without touching the rest of the config.

## Prompt Agent (hosted model + real Python tools)

Once tools are implemented, point at the Python module that exposes them:

```yaml
pipeline:
  inference:
    target:
      model:
        name: azure/gpt-4o-mini
      tools:
        module: examples.agents.health_assistant
```

The toolset, system prompt, and rest of the eval config stay the same — only `tools.toolset` + `tools.simulator` are replaced by `tools.module`. This makes the TDD-then-real progression a one-line change.

## Hosted model only (simple, no tools)

The smallest configuration — model + system prompt, no tools — for sanity-checking the eval pipeline against a Prompt Agent with no tool surface:

```yaml
pipeline:
  inference:
    target:
      model:
        name: azure/gpt-4o-mini
        temperature: 0.0
        max_tokens: 8000
      system_prompt: |
        You are a helpful assistant. Follow the product taxonomy and ask clarifying
        questions when user constraints are missing.
```

## Foundry hosted agent target

When the hosted target is an agent already deployed in Azure AI Foundry — one whose tools and instructions live server-side, not in your YAML — set `target.model` to `azure_ai/agents/<AGENT_ID>`:

```yaml
pipeline:
  inference:
    target:
      model:
        name: azure_ai/agents/asst_xxxxxxxxxxxxxxxx
        temperature: 0.0
        max_tokens: 8000
      # Do NOT set system_prompt or tools here — the hosted agent
      # owns both server-side, and the config parser rejects them
      # for azure_ai/agents/* model names.
```

Requirements:

- `AZURE_AI_API_BASE` env var set to the Foundry project endpoint (not `AZURE_API_BASE`).
- Same Azure auth setup as `azure/*` models — `pip install -e ".[azure-aad]"` and `az login` (or Service Principal env vars). See [Getting Started → Azure AAD](../getting-started.md) for the full setup.

## When to switch to the callable target

The Prompt Agent target is for agents declared in YAML — one model in a runtime-owned tool loop. Once you have a real agent implemented in code (LangGraph, CrewAI, LlamaIndex, OpenAI Agents SDK, AutoGen / MAF, DSPy, custom orchestration, …), switch to the [callable target](callable.md). At that point your code owns the loop, and the recommended OTel-traced integration captures routing, sub-agent decisions, and intermediate tool calls — visibility the Prompt Agent target cannot give you because, by design, you didn't write the loop.
