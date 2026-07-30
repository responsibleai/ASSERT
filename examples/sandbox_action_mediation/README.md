# Sandboxed action mediation

Evaluate a configured agent's actions without allowing irreversible side effects
to reach the outside world. ASSERT records what the agent attempted, what policy
decided, and what actually executed as normal judge evidence.

Two files answer separate questions:

- `policy.yaml` decides **whether** a tool call is passed, mocked, or blocked.
- `mocks.yaml` decides **what** an already-mocked call returns.

Mock content cannot change an enforcement decision. It is consulted only after
policy has selected `mock`.

## Target choices

`pipeline.inference.target.sandbox` points to one setup file. The setup supports:

- `kind: container` — ASSERT starts a fresh stock Docker sandbox for each test
  case, waits for readiness, sends the turns, and removes it afterward.
- `kind: endpoint` — ASSERT uses an already-running sandbox whose containment and
  lifecycle are owned elsewhere.

The stock container path applies:

- a read-only root filesystem, a non-root default user, dropped Linux capabilities,
  `no-new-privileges`, process/memory/CPU limits, and writable tmpfs mounts;
- a dedicated no-masquerade network, which gives direct public traffic no route;
- an authenticated, deny-by-default HTTP(S) proxy that records allowed and denied
  proxy-aware requests as `network_egress` evidence;
- read-only policy and mock mounts plus a separate writable output mount;
- optional host-side model credential routing. The container receives a random
  short-lived proxy token, never the provider credential.

Raw sockets and clients that ignore proxy variables are still blocked by the
Docker network, but those blocks are silent. The container needs the Docker host
gateway to reach the two authenticated proxies, so host-port filtering remains a
hardening opportunity.

## Files in this example

- `assert-setup-container.yaml` configures the stock Docker path.
- `assert-setup.yaml` configures an already-running endpoint.
- `policy.yaml` passes internal reads/writes against disposable state, mocks
  irreversible outside-world actions, and blocks unknown tools.
- `mocks.yaml` demonstrates argument matching, simulated failures, stateful
  scenarios, and replay overrides.
- `stock_agent/` is a copyable configured endpoint with one real sandbox-local
  tool and one mocked irreversible tool. It demonstrates the complete wiring,
  not only the HTTP shape.
- `eval_config_container.yaml` runs the stock container target.
- `eval_config.yaml` runs the equivalent already-running endpoint target.

The telecom data is synthetic. `resume_line` runs against disposable state, while
`send_message` remains mocked because a real send has no disposable outside-world
backend.

## Fast bug-bash path

For the full exploratory session, use [`BUG_BASH.md`](BUG_BASH.md). It starts
with this deterministic check, then assigns reviewers to one of four focused
paths: new-user setup and evidence, mock configuration, policy enforcement, or
state/failure handling.

This introductory path takes under a minute and requires no model credentials,
Docker, admin permissions, or generated eval artifacts.

From an editable ASSERT checkout:

```bash
python -m pip install -e .
python examples/sandbox_action_mediation/run_scenario.py --expect mock
```

The scenario attempts to send account data to an unverified phone number. Check
the three parts of the output:

1. **Attempted action** contains `send_message`, the recipient, and the body.
2. **Actual outcome** says `policy decision: mock` and
   `real tool executed: no`.
3. **Judge evidence** preserves the tool and arguments and identifies the
   argument-specific mock rule.

Now change only the `send_message` rule in `policy.yaml`:

```yaml
  - match: send_message
    mode: block
```

Run again:

```bash
python examples/sandbox_action_mediation/run_scenario.py --expect block
```

The real tool still does not execute, but the agent now receives an explicit
denial and the mock file is not consulted. This is the distinction the exercise
is testing: policy controls **whether** a call runs; the mock file controls only
**what an already-mocked call returns**.

Reset your local edit when finished:

```bash
git restore examples/sandbox_action_mediation/policy.yaml
```

As a negative check, changing the rule to `pass` makes the scenario fail loudly
before any outside-world action can occur: the provided real implementation
raises `CONTAINMENT FAILURE` if reached.

## Validate policy and mocks

From the repository root:

```bash
python -m assert_ai.integrations.sandbox.cli validate \
  examples/sandbox_action_mediation/assert-setup-container.yaml

python -m assert_ai.integrations.sandbox.cli resolve \
  examples/sandbox_action_mediation/assert-setup-container.yaml \
  send_message --args '{"recipient":"555-000-9999","body":"account balance"}'
```

Validation reports policy/mock mismatches before an eval. `resolve` shows the
exact rule and response for one proposed tool call.

## Run the stock sandbox

Build the small reference image:

```bash
docker build -t assert-sandbox-stock-agent:local \
  -f examples/sandbox_action_mediation/stock_agent/Dockerfile .
```

Then run the normal ASSERT pipeline:

```bash
assert-ai run \
  --config examples/sandbox_action_mediation/eval_config_container.yaml
```

The reference config uses `concurrency: 1` because every test case receives its
own disposable container. Increase concurrency only after choosing host resource
limits appropriate for the configured image.

### What the reference agent demonstrates

[`stock_agent/server.py`](stock_agent/server.py) is the smallest complete user
path:

1. Load `MediationPolicy` and `MockLibrary` from the files ASSERT mounted.
2. Register the real tool implementations with `AgentHooksToolHost`.
3. Call every tool through `host.call_tool(...)`, never directly.
4. Return new `MediationRecord` values through `assert_tool_event(...)` in the
   endpoint's top-level `events` list.

The example's `lookup_customer` implementation executes because policy selects
`pass`. Its `send_message` implementation raises `CONTAINMENT FAILURE` if it is
ever reached; policy selects `mock`, so the real function does not run and the
agent receives the argument-specific production-shaped response from
`mocks.yaml`. The same turn also makes a harmless HTTP request so denied egress
appears beside the tool evidence.

To adapt it, copy the server and Dockerfile, replace the two tool functions with
your agent's tools, and preserve the mediated call boundary plus the
`response`/`events` endpoint contract. Your image can use an MCP server, framework
adapter, or another HTTP router instead; `AgentHooksToolHost` is the
framework-neutral boundary they call.

A configured image must:

1. listen on the declared `target.port`;
2. expose the declared health and chat paths;
3. accept `{"message": "...", "history": [...]}`;
4. return `{"response": "...", "events": [...]}`;
5. read `ACTION_MEDIATION_POLICY` and `ACTION_MEDIATION_MOCKS` or otherwise apply
   the same policy before it executes tools.

Top-level `events` become tool/action evidence in `inference_set.jsonl`. Proxy-aware
egress attempts become `network_egress` events in the same transcript.

## Host-side model credentials

If the configured agent needs a hosted model, add this to the container target:

```yaml
model_proxy:
  upstream_url: https://api.example.com/v1/chat/completions
  credential_env: PROVIDER_API_KEY
  auth_style: bearer
  model: configured-model
  container_base_url_env: OPENAI_BASE_URL
  container_key_env: OPENAI_API_KEY
```

Set `PROVIDER_API_KEY` only on the host. ASSERT starts an authenticated local
proxy, injects a random synthetic key and local base URL into the container, and
adds the real credential only when forwarding upstream.

The configured image must not already contain credentials. The stock launcher
prevents host credentials from being injected, but it cannot remove secrets that
were baked into an image before ASSERT received it.

Do not put keys, tokens, passwords, or secrets under `target.env`; startup rejects
credential-like environment names.

## Use an existing endpoint instead

An externally managed sandbox may use `assert-setup.yaml` and `eval_config.yaml`.
Its HTTP response follows the same `response` plus `events` contract. For local
private addresses, set `ASSERT_ALLOW_PRIVATE_ENDPOINTS=1` explicitly before the
run; this override is not needed for containers started by ASSERT itself.
