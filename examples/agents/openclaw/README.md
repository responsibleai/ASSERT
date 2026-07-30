# OpenClaw Connector

This example runs OpenClaw inside Docker and lets `ASSERT` talk to it through `connector: examples.agents.openclaw`. You do not start Docker Compose yourself during a normal evaluation. The connector starts one Compose project per conversation, sends messages with `docker exec`, then removes the project when the conversation closes.

The example requires local Docker with Compose support, plus `AZURE_API_KEY` and `AZURE_API_BASE` in the host environment. The Compose file forwards those variables into the container, and the entrypoint writes OpenClaw's runtime config from them at startup.

Run the bundled example like this:

```bash
cp .env.example .env
source .env
assert-ai run --config examples/prompt_agents/health_assistant_external.yaml
```

If you want to validate the Docker assets without running the full pipeline, build the image directly:

```bash
docker compose -f examples/agents/openclaw/docker-compose.yml build
```

The container currently writes an OpenClaw config that targets `azure-openai/gpt-4o-mini`. If you need a different provider or model, update [entrypoint.sh](entrypoint.sh) and keep the environment contract in [docker-compose.yml](docker-compose.yml) aligned with that change.

## Container isolation

The agent in this container executes untrusted model output during evaluation, so the Compose
file confines it: it runs as the non-root `agent` user (uid 10001), drops all capabilities, sets
`no-new-privileges`, applies CPU, memory and PID limits, and mounts the root filesystem
read-only. The only writable locations are the `openclaw-data` volume at `/home/agent/.openclaw`
and small tmpfs mounts for `/tmp` and `/run`.

If you adapt this example and a tool needs another writable path, add a tmpfs mount for that path
rather than removing `read_only`. Treat these settings as the starting point for a real
deployment, not as a complete sandbox — the container still has unrestricted network egress so
that it can reach the model provider.
