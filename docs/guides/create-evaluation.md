# Create an Evaluation

Use this guide to create `eval_config.yaml` for an ASSERT evaluation run. The evaluation configuration YAML file is the spec that drives an ASSERT evaluation and all the stages of the pipelines.

## Option A: Create with CLI LLM assistant (recommended)

Generate a config YAML file interactively with a [CLI LLM assistant using `assert-ai init`](../cli/commands.md), which will take you through a guided step-by-step multi-turn conversation to gather the basic information to create your evaluation config YAML file.

```bash
assert-ai init --model azure/gpt-5.4
```

> **Helpful CLI options:**
>
> - `--describe "..."` to skip the first question
> - `--from <path>` to extend an existing config
> - `--non-interactive` for one-shot generation
> - `-o <path>` to choose output file
> - `--dry-run` to print YAML without writing

Once you've written the YAML file to path, verify that it looks correct and edit it to add any missing information that may be relevant, then create an evaluation with a single CLI run command:

```bash
assert-ai run --config <path-to-eval_config.yaml>
```

## Option B: Author YAML manually

Minimal shape:

```yaml
suite: my-suite
run: run-1
behavior:
  name: my_behavior
  description: |
    Describe the behaviors and failures to evaluate.
context: |
  Describe your target system, tools, and constraints.
pipeline:
  systematize:
    model:
      name: azure/gpt-5.4-mini
  test_set:
    prompt:
      sample_size: 50
    scenario:
      sample_size: 20
    stratify:
      dimensions:
        - name: user_type
          description: Type of user profile.
    model:
      name: azure/gpt-5.4-mini
  inference:
    target:
      callable: my_package.my_agent:chat_sync
      trace:
        backend: phoenix
        group_by: session.id
  judge:
    model:
      name: azure/gpt-5.4-mini
    dimensions:
      policy_violation:
        description: Did the target violate policy requirements?
        rubric: |
          true = violated requirements
          false = followed requirements
```

Then run the CLI command to run the evaluation:

```bash
assert-ai run --config <path-to-eval_config.yaml>
```

For full configuration schema information, see the [ASSERT Config Reference](../config/schema.md).

## Choose the right target shape

- Use `target.callable` for any Python agent or multi-agent system.
- Prefer `target.callable` with `target.trace` so the judge can inspect tool calls and routing.
- Use `target.model` plus `target.tools` for prompt-and-tool-schema workflows.

For full target details, see the [Target Support Overview](../targets/README.md).
