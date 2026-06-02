# CLI Overview

The core CLI command is:

```bash
assert-ai
```

Use CLI flows to create, run, inspect, and compare evaluations from your local development environment.

## CLI workflow

All evaluations start with designing an evaluation config YAML file. This YAML file specifies the behavior you are trying to test for, along with the AI system information and target information.

> **Tip:** Use the `assert-ai init` command to be guided through a multi-turn conversation with an LLM assistant (using a model you specify) that will gather the relevant basic information to configure and output the baseline YAML file.


```bash
assert-ai init --model azure/gpt-5.4
```

Once you have an evaluation config YAML file, this is all that is needed to run the evaluation pipeline, which will generate the `taxonomy.json`, test set, inference set, and evaluation results in a long running process.

```bash
assert-ai run --config <path-to-eval_config.yaml>
```

Once the evaluation pipeline has finished running, you can list the results and status based on the evaluation suite and the run you want to analyze.

```bash
assert-ai results status <suite> <run>
```

Optionally, you can then compare multiple runs within an evaluation suite.

```bash
assert-ai results compare <suite> <run-a> <run-b>
```

## Learn more

For full syntax and required/optional flags per command, see [CLI Commands](commands.md).
