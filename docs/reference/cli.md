# CLI Reference

ASSERT is CLI-first. All commands assume your virtualenv is activated (see the [README](../../README.md#quickstart-langgraph-travel-planner-any-agent-works-the-same-way) for setup).

## Design a config interactively

```bash
assert-eval init
```

Starts a conversational LLM assistant that asks about your agent, eval goals, and constraints, then proposes a complete `eval_config.yaml`. Use this when you are setting up a new eval from scratch instead of editing YAML by hand.

### Options

| Option | Default | Description |
|---|---|---|
| `--output, -o` | `eval_config.yaml` | Output file path. |
| `--describe` | — | One-line description of the system to evaluate (skips the initial question). |
| `--from` | — | Seed from an existing config (edit/extend mode). |
| `--behavior` | — | Use a built-in behavior preset name. |
| `--judge-preset` | — | Use a built-in judge preset name. |
| `--dimensions` | — | Hint dimension axes for the LLM to elaborate (e.g. `"user_role, language"`). |
| `--model` | `azure/gpt-5.4-mini` | Model for the design agent (any [LiteLLM model string](https://docs.litellm.ai/docs/providers)). |
| `--env-file` | `.env` | Dotenv file for credentials. |
| `--non-interactive` | off | Single-shot mode (no conversation). |
| `--max-turns` | `20` | Maximum conversation turns. |
| `--force` | off | Overwrite existing output file. |
| `--dry-run` | off | Print YAML to stdout without writing a file. |
| `--no-color` | off | Disable colored terminal output. |

### Examples

```bash
# Interactive session — the assistant will ask what you are evaluating
assert-eval init --model azure/gpt-5.4

# Skip the first question with a one-liner
assert-eval init --model azure/gpt-5.4 --describe "A customer-support chatbot with order-lookup and refund tools"

# Edit / extend an existing config
assert-eval init --model azure/gpt-5.4 --from examples/travel_planner_langgraph/eval_config.yaml

# Non-interactive: generate a config in one shot
assert-eval init --model azure/gpt-5.4 --describe "RAG pipeline over internal docs" --non-interactive -o rag_eval.yaml

# Preview the generated YAML without writing a file
assert-eval init --model azure/gpt-5.4 --dry-run

# Use an OpenAI model instead (requires OPENAI_API_KEY)
assert-eval init --model gpt-4.1-mini
```

After `assert-eval init` writes the config, run the pipeline with `assert-eval run --config <path>`.

## Run a config

```powershell
assert-eval run --config examples\travel_planner_langgraph\eval_config.yaml
```

## Re-run one stage

```powershell
assert-eval run --config examples\travel_planner_langgraph\eval_config.yaml --force-stage test_set
```

Use this when you intentionally changed a stage input and want to regenerate downstream artifacts.

## List runs

```powershell
assert-eval results list
```

## Show run status

```powershell
assert-eval results status travel-planner-langgraph-v1 demo-1
```

## Compare runs

```powershell
assert-eval results compare <suite> <run-a> <run-b>
```

## Analyze generated test cases

> Requires either `OPENAI_API_KEY` (default OpenAI embedding backend) or
> the `[analysis]` extra installed for the offline HuggingFace backend
> (`pip install -e ".[analysis]"` then pass `--embed-backend hf` with an HF
> model name, e.g. `all-MiniLM-L6-v2`).

```powershell
# OpenAI backend (default)
assert-eval analysis test-set-metrics --taxonomy artifacts\results\<suite>\taxonomy.json --test_set artifacts\results\<suite>\test_set.jsonl

# Offline HuggingFace backend (no API key)
assert-eval analysis test-set-metrics --taxonomy artifacts\results\<suite>\taxonomy.json --test_set artifacts\results\<suite>\test_set.jsonl --embed-backend hf --embed-model all-MiniLM-L6-v2
```

## Where outputs go

All outputs are written under:

```text
artifacts/results/<suite>/<run>/
```

The CLI does not require a hosted service.
