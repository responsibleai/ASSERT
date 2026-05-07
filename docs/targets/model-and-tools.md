# Model and Tools Target

Use the model and tools target when your "agent" is essentially a hosted model with a system prompt — and optionally a tool schema. The key value: **you can test the prompt and toolset design before any agent code is written.**

## Test-driven prompt + toolset design

You don't need an implemented agent to start evaluating. With a system prompt and a toolset YAML, you can run the full eval pipeline against a *simulated* tool layer — the runtime stands in a model that role-plays plausible tool responses. This lets you:

- iterate on the system prompt against realistic conversations before any orchestration is written
- catch toolset-design mistakes (missing arguments, ambiguous tool descriptions, unreachable tools) early
- decide which behaviors require real backends and which can be specced from the prompt + schema alone

```yaml
pipeline:
  rollout:
    target:
      model:
        name: azure/gpt-5.4-mini
        temperature: 0.0
      system_prompt: |
        You are a helpful assistant. Follow the product policy and ask clarifying
        questions when user constraints are missing.
      tools:
        toolset: examples/agents/health_assistant_tools.yaml
        simulator: azure/gpt-5.4-mini
```

The eval runs end-to-end: policy → test cases → rollout (with simulated tools) → judge verdicts on tool selection, argument correctness, and constraint handling. When the prompt and toolset look right, swap the simulator for real tool implementations (next section) without touching the rest of the config.

## Hosted model with real Python tools

Once tools are implemented, point at the Python module that exposes them:

```yaml
pipeline:
  rollout:
    target:
      model:
        name: azure/gpt-5.4-mini
      tools:
        module: examples.agents.health_assistant
```

The toolset, system prompt, and rest of the eval config stay the same — only `tools.toolset` + `tools.simulator` are replaced by `tools.module`. This makes the TDD-then-real progression a one-line change.

## Hosted model only (smoke)

The smallest configuration — model + system prompt, no tools — for sanity-checking the eval pipeline against a single prompt agent:

```yaml
pipeline:
  rollout:
    target:
      model:
        name: azure/gpt-5.4-mini
        temperature: 0.0
        max_tokens: 8000
      system_prompt: |
        You are a helpful assistant. Follow the product policy and ask clarifying
        questions when user constraints are missing.
```

## When to switch to the callable target

The model+tools target is for prompt-shaped "agents" — one model in a loop with optional tools. Once you have a real agent (LangGraph, CrewAI, LlamaIndex, OpenAI Agents SDK, AutoGen / MAF, DSPy, custom orchestration, …), switch to the [callable target](callable.md). The recommended OTel-traced integration captures tool calls, routing, and intermediate decisions — visibility the model+tools target cannot give you.
