# ASSERT Documentation Overview

Welcome to ASSERT!

## Install

Install the package and run your first evaluation in minutes.

Prerequisite: Python 3.11+

```python
pip install assert_ai
```

## Start here

Begin with the essential overview and first-run walkthrough.

- [Getting Started](getting-started.md): Run your first evaluation with a canonical example
- [Concepts](concepts.md): Understand the concepts of the ASSERT evaluation framework, its pipeline mental model and key terminology.

## How-to guides

Step-by-step guides for common evaluation tasks.

- [Create an Evaluation](guides/create-evaluation.md): Build a new eval config using the interactive assistant or manual YAML.
- [Results Guide](guides/results.md): Interpret scores, metrics, and judge evidence from completed runs.
- [Local Viewer](guides/run-local-viewer.md): Explore suites, runs, transcripts, and metrics in the local web UI.
- [Troubleshooting](guides/troubleshooting.md): Resolve common setup, runtime, and provider integration issues.

## Configuration

Reference docs for writing and tuning eval configuration files.

- [Config Overview](config/overview.md): Learn the structure and components of an eval config YAML file required for running evaluations.
- [Config Schema](config/schema.md): Reference every supported YAML field, type, and default behavior.
- [Best Practices and Limitations](config/best-practices.md): Avoid common pitfalls and understand current pipeline limitations.

## CLI

Command reference for creating, running, and inspecting evaluations.

- [CLI Overview](cli/overview.md): Learn the core CLI workflow for initializing, running, and comparing evaluations.
- [CLI Commands](cli/commands.md): Browse command syntax, options, and examples for each CLI command group.

## Targets

Choose the right target integration path for your system.

- [Target Support Overview](targets/README.md): Compare supported target types and choose the best integration path.
- [Callable Target](targets/callable.md): Connect any Python agent or multi-agent system through a callable entry point.
- [Prompt Agent Target](targets/model-and-tools.md): Configure a hosted model with a system prompt and optional tool schema.
- [Endpoint Target](targets/endpoint.md): Connect an already-running HTTP service or OpenAI-compatible chat endpoint.

## Related docs

Additional docs and examples for deeper exploration.

- [Examples gallery](../examples/README.md)
