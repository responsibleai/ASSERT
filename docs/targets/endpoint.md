# Endpoint Target

Use the endpoint target when the system you want to evaluate is already running as an HTTP service and cannot be imported as a Python callable.

For agents you can invoke from Python, prefer the [callable target with trace capture](callable.md). Endpoint targets are useful for service-hosted systems, local gateways, and OpenAI-compatible servers.

## Simple ASSERT endpoint protocol

The simplest endpoint contract is unchanged. ASSERT sends the latest user turn plus conversation history:

```json
{
  "message": "What should I do next?",
  "history": [
    {"role": "user", "content": "What should I do next?"}
  ]
}
```

The endpoint returns:

```json
{"response": "Here is the answer."}
```

Configure it with a string URL:

```yaml
pipeline:
  inference:
    target:
      endpoint: http://localhost:8787/chat
```

## OpenAI-compatible chat endpoint

If your target exposes an OpenAI-compatible Chat Completions endpoint, configure the endpoint as a mapping:

```yaml
pipeline:
  inference:
    target:
      endpoint:
        protocol: openai_chat
        url: http://localhost:8000/v1/chat/completions
        model: my-agent
        api_key_env: ASSERT_TARGET_API_KEY
        stream: true  # optional; captures streaming endpoint events when returned
```

ASSERT sends:

```json
{
  "model": "my-agent",
  "messages": [
    {"role": "user", "content": "What should I do next?"}
  ]
}
```

and reads `choices[0].message.content` from the response.

`api_key_env` is optional. When set, ASSERT reads that environment variable and sends it as a bearer token. The secret value is not stored in the YAML config.

`stream` is optional and defaults to `false`. When `stream: true`, ASSERT sends `"stream": true` and can read Server-Sent Events from endpoints that return `text/event-stream`. It accumulates normal Chat Completions `choices[0].delta.content` chunks into the assistant answer.

## Tool-call evidence

Endpoint targets are black-box unless the service returns evidence. ASSERT captures tool calls when it can see them:

- `protocol: openai_chat` preserves `choices[0].message.tool_calls` when present.
- `protocol: openai_chat` with `stream: true` preserves supported streaming events, including Hermes `hermes.tool.progress` events, as best-effort tool progress evidence.
- The simple ASSERT endpoint protocol can optionally return `events`:

```json
{
  "response": "The file says hello.",
  "events": [
    {
      "role": "tool_call",
      "tool_name": "read_file",
      "tool_args": {"path": "README.md"},
      "tool_call_id": "call_1"
    },
    {
      "role": "tool_result",
      "tool_name": "read_file",
      "content": "hello",
      "tool_call_id": "call_1"
    }
  ],
  "metadata": {"runtime": "my-agent"}
}
```

ASSERT records those events in the transcript so the judge can cite tool/process evidence. If the endpoint only returns final text, ASSERT cannot infer internal tool use.

## Safety and sandboxing

Endpoint targets run in the target service's own environment. ASSERT does not sandbox an arbitrary external service. Use test data, restricted credentials, and isolated target instances when generated scenarios might cause side effects.
