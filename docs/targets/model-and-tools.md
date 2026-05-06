# Model and Tools Target

Use the model and tools target for simple prompt agents: a hosted model, a system prompt, and optionally tool definitions.

## Hosted model

```yaml
pipeline:
  inference:
    target:
      model:
        name: azure/gpt-5.4-mini
        temperature: 0.0
        max_tokens: 8000
      system_prompt: |
        You are a helpful assistant. Follow the product taxonomy and ask clarifying
        questions when user constraints are missing.
```

This is the fastest way to smoke-test a single model target.

## Hosted model with Python tools

```yaml
pipeline:
  inference:
    target:
      model:
        name: azure/gpt-5.4-mini
      tools:
        module: examples.agents.health_assistant
```

Use this when the tool implementation exists and can run locally.

## Hosted model with simulated tools

```yaml
pipeline:
  inference:
    target:
      model:
        name: azure/gpt-5.4-mini
      tools:
        toolset: examples/agents/health_assistant_tools.yaml
        simulator: azure/gpt-5.4-mini
```

Simulated tools are useful when:

- your prompt agent has a planned tool schema
- real backends are not available yet
- you want to test whether the model calls the right tool and uses plausible results

They are not a replacement for evaluating a real agent or multi-agent system. If you already have a LangGraph, CrewAI, LlamaIndex, OpenAI Agents SDK, AutoGen/MAF, DSPy, or custom-orchestrated agent, prefer the [callable agent target](otel-agent.md) — it accepts any agent or multi-agent system you can invoke from Python, with optional OTel trace capture for richer judge evidence.
