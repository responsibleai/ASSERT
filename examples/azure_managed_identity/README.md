# Azure OpenAI with Managed Identity / Entra ID

A minimal end-to-end eval that proves ASSERT can call Azure OpenAI using
**Entra ID** (managed identity, workload identity, or `az login`) instead of
a static `AZURE_API_KEY`. The eval itself is intentionally tiny — its purpose
is to validate the AAD auth wiring on a real Azure OpenAI deployment.

> **TL;DR:** install the `azure-aad` extra, grant the caller the
> *Cognitive Services OpenAI User* role on the target resource, set
> `ASSERT_AZURE_USE_AAD=1`, and run the example. No code changes required.

## Prerequisites

- A deployed Azure OpenAI resource with at least one chat-completions model
  deployment (e.g. `gpt-5.4-mini`).
- The caller identity has the **Cognitive Services OpenAI User** role on
  that resource (see [Microsoft Learn: Azure RBAC roles for Azure OpenAI](
  https://learn.microsoft.com/azure/ai-services/openai/how-to/role-based-access-control)).
- Python 3.11+ and a clone of this repo.

## Install

```bash
python -m venv .venv
source .venv/bin/activate           # PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[azure-aad]"
```

The `azure-aad` extra pulls in `azure-identity` so ASSERT can mint bearer
tokens through `DefaultAzureCredential`.

## Configure

```bash
cp examples/azure_managed_identity/.env.example .env
# then edit .env and set AZURE_API_BASE to your resource endpoint
```

You only need two env vars to opt into AAD:

| Variable | Required? | Purpose |
|---|---|---|
| `AZURE_API_BASE` | yes | `https://<your-resource>.openai.azure.com/` |
| `ASSERT_AZURE_USE_AAD` | yes (for explicit AAD) | Set to `1` to force AAD even when `AZURE_API_KEY` is also defined. |
| `AZURE_CLIENT_ID` | optional | Pin a specific user-assigned managed identity when several are attached. |

Leave `AZURE_API_KEY` unset (or just ignore it) when running this example.

## Pick an auth path

| Where you run | What to do |
|---|---|
| Local dev | `az login`, then `export ASSERT_AZURE_USE_AAD=1`. `DefaultAzureCredential` picks up your `az` session. |
| GitHub Actions / CI | Configure an OIDC federated credential against your subscription and let the runner mint tokens automatically. `azure/login@v2` is the standard step. |
| App Service / Container Apps / AKS / VM | Attach a system- or user-assigned managed identity and grant it the role above. Set `AZURE_CLIENT_ID` if multiple user-assigned identities are attached. |

## Run

```bash
assert-ai run --config examples/azure_managed_identity/eval_config.yaml
assert-ai results status azure-mi-auth-demo aad-smoke-1
```

A successful run writes artifacts to:

```text
artifacts/results/azure-mi-auth-demo/aad-smoke-1/
```

If the wiring is wrong you will see one of these instead:

- `LLMAuthError: ... azure-identity package is not installed` → run
  `pip install -e ".[azure-aad]"`.
- `401` with a hint about *Cognitive Services OpenAI User* → the credential
  resolved but RBAC is missing on the resource.
- `401` with a hint to install the `azure-aad` extra → you are in opportunistic
  fallback mode without `azure-identity`. Install the extra or set
  `AZURE_API_KEY`.

## How it works

ASSERT resolves an auth mode once at process start:

| You set | Mode | Behavior |
|---|---|---|
| `ASSERT_AZURE_USE_AAD=1` | `aad` (explicit) | Always inject `azure_ad_token_provider`. Missing `azure-identity` fails loud. |
| `AZURE_API_KEY=...` (flag unset) | `key` | Today's default. Zero behavior change. |
| Neither | `aad-fallback` | Best-effort AAD; if `azure-identity` is missing, LiteLLM's own error is rewritten with an install hint. |

In `aad` / `aad-fallback` modes the model_client injects a cached
`DefaultAzureCredential`-backed bearer-token provider into every LiteLLM
payload that targets an `azure/*` model. The user's explicit
`extra_kwargs.azure_ad_token_provider` always wins.

See [`docs/getting-started.md`](../../docs/getting-started.md#authenticating-azure-openai-with-managed-identity)
for the canonical reference.
