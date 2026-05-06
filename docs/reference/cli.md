# CLI Reference

Adaptive Eval is CLI-first. All commands assume your virtualenv is activated (see the [README](../../README.md#quickstart-langgraph-travel-planner-any-agent-works-the-same-way) for setup).

## Run a config

```powershell
p2m run --config examples\travel_planner_langgraph\eval_config.yaml
```

## Re-run one stage

```powershell
p2m run --config examples\travel_planner_langgraph\eval_config.yaml --force-stage seeds
```

Use this when you intentionally changed a stage input and want to regenerate downstream artifacts.

## List runs

```powershell
p2m results list
```

## Show run status

```powershell
p2m results status travel-planner-langgraph-v1 demo-1
```

## Compare runs

```powershell
p2m results compare <suite> <run-a> <run-b>
```

## Analyze generated test cases

> Requires either `OPENAI_API_KEY` (default OpenAI embedding backend) or
> the `[analysis]` extra installed for the offline HuggingFace backend
> (`pip install -e ".[analysis]"` then pass `--embed-backend hf` with an HF
> model name, e.g. `all-MiniLM-L6-v2`).

```powershell
# OpenAI backend (default)
p2m analysis seed-metrics --policy artifacts\results\<suite>\policy.json --seeds artifacts\results\<suite>\seeds.jsonl

# Offline HuggingFace backend (no API key)
p2m analysis seed-metrics --policy artifacts\results\<suite>\policy.json --seeds artifacts\results\<suite>\seeds.jsonl --embed-backend hf --embed-model all-MiniLM-L6-v2
```

## Where outputs go

All outputs are written under:

```text
artifacts/results/<suite>/<run>/
```

The CLI does not require a hosted service.
