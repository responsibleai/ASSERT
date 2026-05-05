# Pipeline Configs

Run any config with:

```bash
uv run p2m run --config examples/pipes/<name>.yaml
```

## Simple target shapes

These configs evaluate a health assistant with simple hosted-model and model+tools targets. They are useful smoke tests, but the flagship framework-agent example is `examples\travel_planner_langgraph\eval_config.yaml`.

| Config | Target | What it demonstrates |
|---|---|---|
| `health_assistant.yaml` | hosted, no tools | Plain chat model. Simplest full pipeline. |
| `health_assistant_sandbox.yaml` | hosted + sandbox-backed module | Real Python tools via `examples.agents.health_assistant`, with one Docker container per conversation. Requires Docker and may pull `python:3.11-bookworm` on first use. |
| `health_assistant_simulated_tools.yaml` | hosted + fixed toolset | Tool schemas from a YAML file, simulator model generates responses. |
| `health_assistant_generated_tools.yaml` | hosted + per-seed tools | Each seed carries its own tool definitions. |

For real framework agents, prefer `target.callable` plus OTel trace capture. See [`..\travel_planner_langgraph\eval_config.yaml`](../travel_planner_langgraph/eval_config.yaml).
