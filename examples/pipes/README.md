# Pipeline Configs

Run any config with:

```powershell
assert-eval run --config examples\pipes\<name>.yaml
```

(Assumes your virtualenv is activated. See the [README](../../README.md#quickstart-langgraph-travel-planner-any-agent-works-the-same-way) for setup.)

All shipped pipe configs default to 10 total test cases (`prompt.sample_size: 5` + `scenario.sample_size: 5`) and are intended to finish in under 5 minutes on a typical Azure OpenAI deployment. For a larger smoke run, use `--override test_set.sample_size=20`; expect about 5-10 minutes after any Docker image pull or build is complete.

## Simple target shapes

These configs evaluate a health assistant with simple hosted-model and Prompt Agent targets. They are useful smoke tests, but the flagship framework-agent example is `examples\travel_planner_langgraph\eval_config.yaml`.

| Config | Target | Default / full size | What it demonstrates |
|---|---|---|---|
| `health_assistant.yaml` | hosted, no tools | Default n=10 (<5 min); larger n=20 (~5-10 min) | Plain chat model. Simplest full pipeline. |
| `health_assistant_sandbox.yaml` | hosted + sandbox-backed module | Default n=10 (<5 min after Docker warm-up); larger n=20 (~5-10 min) | Real Python tools via `examples.agents.health_assistant`, with one Docker container per conversation. **Requires Docker Desktop running** and may pull `python:3.11-bookworm` on first use. |
| `health_assistant_simulated_tools.yaml` | hosted + fixed toolset | Default n=10 (<5 min); larger n=20 (~5-10 min) | Tool schemas from a YAML file, simulator model generates responses. |
| `health_assistant_generated_tools.yaml` | hosted + per-test-case tools | Default n=10 (<5 min); larger n=20 (~5-10 min) | Each test case carries its own tool definitions. |
| `health_assistant_external.yaml` | external connector | Default n=10 (<5 min after Docker warm-up); larger n=20 (~5-10 min) | Demonstrates the external-agent connector path. **Requires Docker Desktop running.** Not the recommended path for new customer onboarding — prefer `target.callable` instead. |

> **Docker prerequisite.** The `_sandbox.yaml` and `_external.yaml` variants spin up containers per conversation. Make sure Docker Desktop is running before invoking those configs, or you'll see "docker daemon unavailable" errors.

For any agent or multi-agent system, use `target.callable` with OTel trace capture so the judge sees tool calls and routing. See [`..\travel_planner_langgraph\eval_config.yaml`](../travel_planner_langgraph/eval_config.yaml) for the recommended integration shape.
