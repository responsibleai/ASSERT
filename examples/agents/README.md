# Agents

This directory holds tool modules and tool schemas used by the example pipeline configs in [`../pipes/`](../pipes/). The flagship customer integration path is `target.callable` — see [`../travel_planner_langgraph/`](../travel_planner_langgraph/).

ASSERT supports three ways to give a target access to tools or external systems.

| Pattern | What happens | Config key | Example file |
|---|---|---|---|
| Tool module | ASSERT calls Python functions and returns real results to the model | `tools.module` | [health_assistant.py](health_assistant.py) |
| Toolset | ASSERT declares tool schemas in YAML; a second model fakes the results | `tools.toolset` | [health_assistant_tools.yaml](health_assistant_tools.yaml) |
| External connector (advanced) | Your agent owns the conversation; ASSERT records and scores it. **Not the recommended onboarding path** — prefer `target.callable` instead. | `connector` | [openclaw/README.md](openclaw/README.md) |

## Files

| File | What it does |
|---|---|
| `health_assistant.py` | Docker-backed sandbox tool module with medication lookup, interaction checks, and dosage assessment |
| `health_assistant_tools.yaml` | Tool schemas for simulated-tool runs (same surface as the sandbox-backed module, but results are faked) |
| `openclaw/` | External connector for the OpenClaw coding agent, plus its Docker assets (advanced) |

## How configs reference these files

Tool modules and connectors use Python dotted paths:

```yaml
# real tools
pipeline:
  inference:
    target:
      tools:
        module: examples.agents.health_assistant

# external agent (advanced — prefer target.callable)
pipeline:
  inference:
    connector: examples.agents.openclaw
```

Toolsets use file paths:

```yaml
# simulated tools
pipeline:
  inference:
    target:
      tools:
        toolset: examples/agents/health_assistant_tools.yaml
        simulator: azure/gpt-4o
```

The `health_assistant.py` module requires Docker locally. On first use, Docker may need to pull `python:3.11-bookworm`. The config still uses the same `tools.module` dotted path.

The `examples.agents.openclaw` connector also requires Docker with Compose support. Each inference conversation gets its own Compose project and container. On first use, Docker Compose builds the image from `openclaw/Dockerfile`, which pulls `node:24-bookworm` and installs `openclaw@latest`. The container reads `AZURE_API_KEY` and `AZURE_API_BASE` from the host environment at startup to configure OpenClaw. See [openclaw/README.md](openclaw/README.md) for the Docker-specific setup.

See [pipes/](../pipes/) for complete configs that use each pattern.
