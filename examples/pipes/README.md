# Pipeline Configs

Run any config with:

```powershell
assert-eval run --config examples/pipes/<name>.yaml
```

(Assumes your virtualenv is activated. See the [README](../../README.md#quickstart-langgraph-travel-planner-any-agent-works-the-same-way) for setup.)

## Simple target shapes

These configs evaluate a health assistant with simple hosted-model and Prompt Agent targets. They are useful smoke tests, but the flagship framework-agent example is `examples\travel_planner_langgraph\eval_config.yaml`.

| Config | Target | What it demonstrates |
|---|---|---|
| `health_assistant.yaml` | hosted, no tools | Plain chat model. Simplest full pipeline. |
| `health_assistant_sandbox.yaml` | hosted + sandbox-backed module | Real Python tools via `examples.agents.health_assistant`, with one Docker container per conversation. **Requires Docker Desktop running** and may pull `python:3.11-bookworm` on first use. |
| `health_assistant_simulated_tools.yaml` | hosted + fixed toolset | Tool schemas from a YAML file, simulator model generates responses. |
| `health_assistant_generated_tools.yaml` | hosted + per-test-case tools | Each test case carries its own tool definitions. |
| `health_assistant_external.yaml` | external connector | Demonstrates the external-agent connector path. **Requires Docker Desktop running.** Not the recommended path for new customer onboarding — prefer `target.callable` instead. |

> **Docker prerequisite.** The `_sandbox.yaml` and `_external.yaml` variants spin up containers per conversation. Make sure Docker Desktop is running before invoking those configs, or you'll see "docker daemon unavailable" errors.

For any agent or multi-agent system, use `target.callable` with OTel trace capture so the judge sees tool calls and routing. See [`..\travel_planner_langgraph\eval_config.yaml`](../travel_planner_langgraph/eval_config.yaml) for the recommended integration shape.
