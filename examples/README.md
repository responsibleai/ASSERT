# Examples

Runnable pipeline configs and supporting assets. Start here to run the repo's main example.

## First run

```bash
uv venv && uv sync
cp .env.example .env
source .env

uv run p2m run --config examples/pipes/health_assistant.yaml
```

## Which config to start with

| Goal | Config |
|---|---|
| Plain chat model, no tools | `pipes/health_assistant.yaml` |
| Hosted target with sandbox-backed tools | `pipes/health_assistant_sandbox.yaml` (requires Docker) |
| Hosted target with simulated tools from a fixed schema | `pipes/health_assistant_simulated_tools.yaml` |
| Hosted target with per-seed tool definitions | `pipes/health_assistant_generated_tools.yaml` |
| External agent via connector | `pipes/health_assistant_external.yaml` (requires Docker Compose and Azure model env vars) |

See [pipes/README.md](pipes/README.md) for what each config demonstrates.

## Layout

```text
examples/
├── pipes/             pipeline configs
├── concepts/           concept definitions loaded by `concept: <name>`
└── agents/            tool modules, toolsets, and external connectors
```

See [concepts/README.md](concepts/README.md) for available concept definitions.
