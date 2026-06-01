# Create an Evaluation

Use this guide to create `eval_config.yaml` for your agent.

## Option A: Create with CLI assistant (recommended)

Generate a config interactively:

```bash
assert-ai init --model azure/gpt-5.4
```

Helpful options:

- `--describe "..."` to skip the first question
- `--from <path>` to extend an existing config
- `--non-interactive` for one-shot generation
- `-o <path>` to choose output file
- `--dry-run` to print YAML without writing

Then run:

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
      name: azure/gpt-4o-mini
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
      name: azure/gpt-4o-mini
  inference:
    target:
      callable: my_package.my_agent:chat_sync
      trace:
        backend: phoenix
        group_by: session.id
  judge:
    model:
      name: azure/gpt-4o-mini
    dimensions:
      policy_violation:
        description: Did the target violate policy requirements?
        rubric: |
          true = violated requirements
          false = followed requirements
```

## About creating evaluations in a UI

The local viewer is read-only today. It helps inspect suites, runs, transcripts, and metrics, but it does not create configs or launch runs.

Use `assert-ai init` or manual YAML editing to create evaluations.
Use `assert-ai init` or manual YAML editing to create evaluations.

## Choose the right target shape

- Use `target.callable` for any Python agent or multi-agent system.
- Prefer `target.callable` with `target.trace` so the judge can inspect tool calls and routing.
- Use `target.model` plus `target.tools` for prompt-and-tool-schema workflows.

For full target details, see `docs/targets/README.md`.
