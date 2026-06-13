# Task: Azure Managed Identity auth for Azure OpenAI

**Status:** In progress (PR 1 not yet started)
**Owner:** unassigned — any agent can pick up
**Last updated:** 2026-06-09

---

## Goal

Add opt-in Azure Active Directory (Entra ID) authentication to the LiteLLM
transport so users on Azure VMs, Container Apps, AKS pods, or local dev
machines with `az login` can run ASSERT against Azure OpenAI without
provisioning an `AZURE_API_KEY` secret.

**Strictly additive.** API-key auth keeps working exactly as today — no
behavior change for current users.

## Why now

Users running ASSERT on Azure-hosted compute have reported friction obtaining
and rotating `AZURE_API_KEY` values. Managed Identity + Entra ID is the
default-recommended auth path for Azure OpenAI and works in every host
environment that matters (local dev via `az login`, GitHub Actions via OIDC,
Azure VM/Container Apps/AKS via assigned MI).

## Scope

### In scope (this task)

PR 1 only — the core auth wiring inside `assert_ai/core/`:

- New optional dependency `azure-aad` (pulls in `azure-identity`).
- New helper module `assert_ai/core/azure_auth.py` for credential + provider.
- Wire the token provider into the LiteLLM payload builders for `azure/*`
  models.
- Sanitize new auth fields in logs/exports.
- User-facing docs for the new auth mode.

### Out of scope (filed as follow-ups below)

- Migrating the `regression.yml` CI workflow to GitHub OIDC.

The 14 existing example agents stay on `AZURE_API_KEY` — we are not
retrofitting them. Instead, PR 1 adds **one new minimal example** that
demonstrates the AAD path (see commit 1.6 below). This doubles as live
validation that the wiring in commit 1.3 actually works against a real
Azure OpenAI deployment, not just against unit-test mocks.

Follow-ups are tracked under [Follow-ups](#follow-ups) below.

## Design

### Auth-mode resolution (precedence)

| # | Condition                                       | Mode used      | Notes                                |
|---|-------------------------------------------------|----------------|--------------------------------------|
| 1 | `ASSERT_AZURE_USE_AAD=1`                        | `aad`          | Explicit opt-in; ignores any key.    |
| 2 | `AZURE_API_KEY` set (and flag not set)          | `key`          | Today's behavior, unchanged.         |
| 3 | Neither set, `azure/*` model requested          | `aad-fallback` | BYO MI / `az login` / SP.            |
| 4 | Non-Azure provider                              | unchanged      | Per-provider keys as today.          |

A single `INFO` log line on first request records the chosen mode:
`auth_mode=key|aad|aad-fallback model=azure/<deployment>`.

### Why payload-level injection (not the LiteLLM global flag)

LiteLLM exposes both `litellm.enable_azure_ad_token_refresh = True` (global)
and per-call `azure_ad_token_provider=` (payload kwarg). We use the per-call
kwarg because:

- It scopes AAD to the requests that need it (mixed Azure + non-Azure judge
  and target models in one process work cleanly).
- It binds to a specific user-assigned MI via `AZURE_CLIENT_ID` without
  trusting LiteLLM's global discovery order.
- The diff is ~15 lines inside `_build_chat_payload` /
  `_build_responses_payload` — no changes to the three `litellm.acompletion`
  call sites at `assert_ai/core/model_client.py:1452`, `:1536`, `:1583`.

### Required RBAC (documented, not provisioned)

The calling identity needs the `Cognitive Services OpenAI User` role on the
target Azure OpenAI resource. ASSERT does not provision Azure resources —
each user/team owns their own AOAI setup.

---

## Implementation plan (PR 1)

Each box is one focused commit. Conventional Commits prefixes shown.
Every commit must pass `pytest tests/` on its own.

### Commit 1.1 — `chore: add azure-identity as optional extra`

- [ ] Edit `pyproject.toml`:
  - Add new `[project.optional-dependencies]` entry:
    ```toml
    azure-aad = ["azure-identity>=1.19.0"]
    ```
  - Append `"azure-identity>=1.19.0"` to the existing `examples` extra so
    example agents (when refactored in the follow-up) get it automatically.
- [ ] Do not modify the base `dependencies =` list — keeps default install
  slim.
- [ ] Verify `pip install -e ".[azure-aad]"` resolves cleanly.

### Commit 1.2 — `feat: add azure_auth helper (no callers yet)`

- [ ] Create `assert_ai/core/azure_auth.py` (~50 lines). API surface:

  ```python
  Mode = Literal["key", "aad", "aad-fallback", "none"]

  def resolve_azure_auth_mode(env: Mapping[str, str] | None = None) -> Mode:
      """Pure function over env vars. Implements the precedence table."""

  def get_azure_token_provider() -> Callable[[], str] | None:
      """Return a cached token provider, or None if azure-identity missing."""
  ```

- [ ] Constants:
  - Scope: `"https://cognitiveservices.azure.com/.default"`
  - Env vars consumed: `ASSERT_AZURE_USE_AAD`, `AZURE_API_KEY`,
    `AZURE_CLIENT_ID` (read by `DefaultAzureCredential` natively).
- [ ] Lazy-import `azure.identity` — return `None` from
  `get_azure_token_provider()` if the package is not installed, so the
  default ASSERT install stays slim.
- [ ] Use `DefaultAzureCredential(exclude_interactive_browser_credential=True,
  exclude_visual_studio_code_credential=True)` to avoid CI hangs.
- [ ] Cache the credential + provider at module scope (single instance per
  process; `azure-identity` handles its own token caching/refresh).
- [ ] **No imports from `model_client`** — `azure_auth` is a leaf module.

- [ ] Create `tests/test_azure_auth.py`:
  - [ ] Precedence matrix: 4 env-var states → 4 expected modes.
  - [ ] `get_azure_token_provider()` returns `None` when `azure-identity` is
    not importable (patch `sys.modules`).
  - [ ] Stub `DefaultAzureCredential` to verify no real network calls.

### Commit 1.3 — `feat: wire AAD into model_client for azure/* models`

- [ ] In `assert_ai/core/model_client.py`, add `_configure_azure_auth_mode()`
  immediately after `_normalize_azure_api_base()` (around line 1630):
  - Resolves mode once at import.
  - Logs the decision at `INFO`.
  - Pre-warms the token provider when mode is `aad` or `aad-fallback` so
    the first request doesn't pay the 200–600 ms cold-start cost.

- [ ] In `_build_chat_payload` (line 537) and `_build_responses_payload`
  (line 559), after the existing payload assembly:
  - If `_model_family(model) == "azure"` (existing helper at line 362)
    AND resolved mode is `aad` or `aad-fallback`:
    - Set `payload["azure_ad_token_provider"] = <cached provider>`.
    - Do **not** set `api_key` (LiteLLM will use the provider).
  - Otherwise: payload byte-identical to today.

- [ ] Extend the `litellm.AuthenticationError` handler at line 833 to append
  this hint when AAD mode is active:
  > "AAD auth in use — verify the principal has the 'Cognitive Services
  > OpenAI User' role on the target Azure OpenAI resource."

- [ ] Extend `tests/test_model_client.py` (or add a new file) with the
  six-row test matrix in [Acceptance criteria](#acceptance-criteria).
- [ ] Verify `tests/test_rate_limit_retry.py` still passes — it mocks
  `litellm.acompletion`, so payload changes should be transparent, but
  re-run to confirm.

### Commit 1.4 — `security: redact azure_ad_token* in sanitize_payload`

- [ ] Find `sanitize_payload` (used by `tests/test_security.py:16`). Add
  `azure_ad_token` and `azure_ad_token_provider` to the redaction key set.
- [ ] The provider field is a callable — redact defensively so its `repr`
  (which may include bound credential metadata) never reaches logs.
- [ ] Extend `tests/test_security.py` around line 230 with two new cases
  mirroring the existing `test_redacts_api_key`.

### Commit 1.5 — `docs: managed identity auth for Azure OpenAI`

- [ ] Add a new "Authenticating with Managed Identity" section to
  `docs/getting-started.md`:
  - The precedence table from [Design](#auth-mode-resolution-precedence).
  - Required RBAC: `Cognitive Services OpenAI User`.
  - Install hint: `pip install -e ".[azure-aad]"`.
  - Platform quickstarts (one short snippet each):
    - Local dev (`az login` is enough).
    - Azure VM / Container Apps with system-assigned MI (no config needed).
    - Azure VM / AKS with user-assigned MI (set `AZURE_CLIENT_ID`).
    - GitHub Actions: forward-reference to the follow-up OIDC migration.
  - Point readers at the new `examples/azure_managed_identity/` quickstart
    (added in commit 1.6) as the runnable companion.
- [ ] Update `.env.example` with commented hints:
  ```
  # ASSERT_AZURE_USE_AAD=1
  # AZURE_CLIENT_ID=<user-assigned-managed-identity-client-id>
  ```
- [ ] Add a one-paragraph callout to `README.md`'s auth section linking to
  the new docs page.
- [ ] Update `AGENTS.md`: extend the "never read/print `AZURE_API_KEY`" rule
  to also cover `azure_ad_token`, `azure_ad_token_provider`, and any
  bearer token returned by `get_azure_token_provider()`.

### Commit 1.6 — `feat(examples): add azure_managed_identity quickstart`

A single new minimal example that exercises the AAD path end-to-end. All
fourteen existing example agents stay untouched — this is purely additive.

- [ ] Create `examples/azure_managed_identity/`:
  - [ ] `eval_config.yaml` — smallest viable config: one behavior, one
    target callable, one judge dimension. Uses an `azure/*` model name.
    No `AZURE_API_KEY` reference anywhere.
  - [ ] `agent.py` — a tiny callable target (~20 lines) that wraps a
    single Azure OpenAI chat call. Pulls credentials via the same
    `DefaultAzureCredential` path the core uses, so the example mirrors
    real usage rather than re-implementing auth.
  - [ ] `README.md` (~50 lines) covering:
    1. Prerequisite: `pip install -e ".[azure-aad,examples]"`.
    2. RBAC: assign `Cognitive Services OpenAI User` to your principal
       on the target Azure OpenAI resource.
    3. Auth setup (pick one):
       - Local: `az login`.
       - User-assigned MI on Azure compute: set `AZURE_CLIENT_ID`.
       - System-assigned MI: no config.
    4. Run: `ASSERT_AZURE_USE_AAD=1 assert-ai run --config examples/azure_managed_identity/eval_config.yaml`.
    5. Expected output: one passing judge score.
  - [ ] `.env.example` — show only commented placeholders
    (`ASSERT_AZURE_USE_AAD`, `AZURE_API_BASE`, `AZURE_CLIENT_ID`). Never
    include real values.
- [ ] Add a row for the new example to `examples/README.md`'s selection
  guide table.
- [ ] Do not register this example in `tests/` as an automated test —
  it needs a real Azure OpenAI deployment. README documents it as a
  manual smoke test.

---

## Acceptance criteria

Unit tests must cover all six rows; the feature is done when they pass plus
the docs build cleanly:

| # | Env state                                            | Model              | Expected                                                         |
|---|------------------------------------------------------|--------------------|------------------------------------------------------------------|
| 1 | `AZURE_API_KEY=xxx`                                  | `azure/gpt-4o`     | Works. Payload identical to today. (Regression guard.)           |
| 2 | `AZURE_API_KEY=xxx` + `ASSERT_AZURE_USE_AAD=1`       | `azure/gpt-4o`     | AAD used. No `api_key` in payload. Log: `mode=aad`.              |
| 3 | Neither set                                          | `azure/gpt-4o`     | AAD fallback. Log: `mode=aad-fallback`.                          |
| 4 | Neither set + `azure-identity` not installed         | `azure/gpt-4o`     | Clear error: install `pip install ".[azure-aad]"`.               |
| 5 | `ASSERT_AZURE_USE_AAD=1`                             | `openai/gpt-4o`    | Unchanged — `OPENAI_API_KEY` used. No AAD on the call.           |
| 6 | `OPENAI_API_KEY=xxx`                                 | `openai/gpt-4o`    | Unchanged.                                                       |

Additionally:

- [ ] No `azure_ad_token*` field ever appears in sanitized logs.
- [ ] `pip install -e .` (no extra) still works without `azure-identity`.
- [ ] `import assert_ai.core.model_client` works without `azure-identity`.
- [ ] `docs/getting-started.md` renders correctly in the docs build.

## Manual validation against a real Azure OpenAI resource

PR 1 lands without requiring this, but recommended before announcing:

- [ ] On a workstation with `az login` and the right RBAC role, run any
  small example with `ASSERT_AZURE_USE_AAD=1` and an `azure/*` model.
  Confirm a successful response.
- [ ] Confirm no `AZURE_API_KEY` was present in the environment.

## Touchpoints reference

| File                                                              | Line(s)            | Change                                        |
|-------------------------------------------------------------------|--------------------|-----------------------------------------------|
| `pyproject.toml`                                                  | optional-deps      | Add `azure-aad` extra.                        |
| `assert_ai/core/azure_auth.py`                                    | new file           | Helper module.                                |
| `assert_ai/core/model_client.py`                                  | 362 (read), 537, 559, 833, 1608–1630 | Inject provider, hint on auth error. |
| `tests/test_azure_auth.py`                                        | new file           | Precedence + helper tests.                    |
| `tests/test_model_client.py`                                      | extend             | Six-row matrix.                               |
| `tests/test_security.py`                                          | around 230         | Two redaction cases.                          |
| `tests/test_rate_limit_retry.py`                                  | re-run only        | Verify no regression.                         |
| `docs/getting-started.md`                                         | new section        | User-facing auth guide.                       |
| `.env.example`                                                    | append             | Commented hints.                              |
| `README.md`                                                       | auth section       | One paragraph + link.                         |
| `AGENTS.md`                                                       | safety section     | Extend redaction rule.                        |
| `examples/azure_managed_identity/eval_config.yaml`                | new file           | Minimal AAD example config.                   |
| `examples/azure_managed_identity/agent.py`                        | new file           | ~20-line callable target.                     |
| `examples/azure_managed_identity/README.md`                       | new file           | Setup + run instructions.                     |
| `examples/azure_managed_identity/.env.example`                    | new file           | Commented placeholders only.                  |
| `examples/README.md`                                              | append row         | Add to selection guide table.                 |

---

## Follow-ups (not in PR 1)

Deferred; create separately when this task lands.

### Follow-up A — Migrate CI to GitHub OIDC

- Edit `.github/workflows/regression.yml`:
  - Add `permissions: { id-token: write, contents: read }`.
  - Add `azure/login@v2` step using federated identity.
  - Set `ASSERT_AZURE_USE_AAD=1` in the test step env.
  - Drop the `AZURE_API_KEY` secret reference after one green cycle.
- Document the federated-identity-credential setup in `CONTRIBUTING.md`
  (one-time human action in the team's Azure subscription).
- `build.yml`, `deploy-pages.yml`, `scorecard.yml` need no changes
  (verified — they don't reference `AZURE_API_KEY`).

---

## Progress log

Append a dated entry whenever an agent makes meaningful progress.

- **2026-06-09** — Task created. PR 1 scope frozen; examples + CI deferred.
