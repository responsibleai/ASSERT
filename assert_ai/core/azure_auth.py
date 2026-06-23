# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Optional Entra ID / Managed Identity auth for Azure OpenAI calls.

This module is a leaf helper used by ``assert_ai.core.model_client`` to
inject an ``azure_ad_token_provider`` into LiteLLM payloads for
``azure/*`` models. It is strictly additive: when the user has set
``AZURE_API_KEY`` (and not opted into AAD), the legacy key-based path
runs unchanged.

Auth-mode precedence (explicit AAD flag wins, then key, then auto-fallback):

1. ``ASSERT_AZURE_USE_AAD=1`` → always AAD, even if a key is also set.
2. ``AZURE_API_KEY`` set (and flag not set) → key auth, unchanged.
3. Neither set → AAD via ``DefaultAzureCredential`` (auto-fallback).

The ``azure-identity`` package is an *optional* dependency
(``pip install assert-ai[azure-aad]``). When it is not installed and
the resolved mode is AAD, :func:`get_azure_token_provider` returns
``None`` so callers can produce a clear install-hint error instead of
crashing on import.

This module deliberately has no imports from ``assert_ai.core.*`` so it
stays cheap to load and safe to import from anywhere in the package.
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Literal, Mapping

log = logging.getLogger(__name__)

Mode = Literal["key", "aad", "aad-fallback"]

#: Standard scope for Azure OpenAI / Cognitive Services data-plane tokens.
AZURE_OPENAI_SCOPE = "https://cognitiveservices.azure.com/.default"

#: Scope for Azure AI Foundry data-plane tokens (Foundry projects, hosted
#: agents, and other ``azure_ai/*`` LiteLLM routes). Distinct from the
#: Cognitive Services scope above because the Foundry control plane sits
#: behind a different audience.
AZURE_FOUNDRY_SCOPE = "https://ai.azure.com/.default"

#: Env var users set to force AAD even when ``AZURE_API_KEY`` is present.
ENV_USE_AAD_FLAG = "ASSERT_AZURE_USE_AAD"

_TRUTHY = {"1", "true", "yes", "on"}

# Maps the azure-identity credential class name to a short, friendly
# label so users see "az login" instead of "AzureCliCredential" in the
# one-line provenance log. Anything not in this map falls back to the
# raw class name — better than silence, and unlikely in practice.
#
# Note: VisualStudioCodeCredential is intentionally absent — we set
# ``exclude_visual_studio_code_credential=True`` on DefaultAzureCredential
# so it is never resolved from in practice.
_FRIENDLY_CRED_LABELS: Mapping[str, str] = {
    "AzureCliCredential": "az login",
    "AzureDeveloperCliCredential": "azd login",
    "AzurePowerShellCredential": "Azure PowerShell",
    "ManagedIdentityCredential": "managed identity",
    "WorkloadIdentityCredential": "workload identity",
    "EnvironmentCredential": "service principal (env vars)",
    "SharedTokenCacheCredential": "shared token cache",
}

# DefaultAzureCredential is expensive to construct repeatedly and
# azure-identity already handles its own in-process token caching, so
# provider callables are built lazily and reused for the process
# lifetime. We key the cache on scope because Azure OpenAI and Azure AI
# Foundry data planes accept tokens for different audiences; the
# underlying ``DefaultAzureCredential`` is reused across scopes.
_PROVIDER_CACHE: dict[str, Callable[[], str] | None] = {}
_CACHED_CREDENTIAL: object | None = None
#: True after the first import attempt for ``azure.identity`` has
#: completed, regardless of whether it succeeded. Lets us short-circuit
#: repeat imports without re-introducing per-scope ImportError costs.
_IDENTITY_IMPORT_ATTEMPTED = False
_IDENTITY_AVAILABLE = False


def _is_truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _TRUTHY


def resolve_azure_auth_mode(env: Mapping[str, str] | None = None) -> Mode:
    """Decide which auth mode applies to Azure OpenAI calls.

    Pure function over the environment; safe to call repeatedly. The
    caller is responsible for checking whether the in-flight request is
    actually an ``azure/*`` model before acting on the returned mode.

    Defaults to ``os.environ`` when no override is provided.
    """
    env = env if env is not None else os.environ
    if _is_truthy(env.get(ENV_USE_AAD_FLAG)):
        return "aad"
    if (env.get("AZURE_API_KEY") or "").strip():
        return "key"
    return "aad-fallback"


def get_azure_token_provider(
    scope: str = AZURE_OPENAI_SCOPE,
) -> Callable[[], str] | None:
    """Return a cached bearer-token callable for ``scope``, or ``None``.

    Returns ``None`` (without raising) when ``azure-identity`` is not
    installed. Callers should treat ``None`` as "AAD requested but
    optional dependency missing" and surface an actionable install hint.

    The default scope (:data:`AZURE_OPENAI_SCOPE`) covers ``azure/*``
    LiteLLM models. Pass :data:`AZURE_FOUNDRY_SCOPE` for ``azure_ai/*``
    routes (Foundry hosted agents, embeddings, chat). Each scope gets
    its own cached provider; all providers share a single
    ``DefaultAzureCredential`` instance so adding a second scope does
    not pay for a second credential-chain probe.

    Honors ``AZURE_CLIENT_ID`` natively via ``DefaultAzureCredential``
    so users can select a specific user-assigned managed identity
    without passing anything to ASSERT.
    """
    global _CACHED_CREDENTIAL, _IDENTITY_IMPORT_ATTEMPTED, _IDENTITY_AVAILABLE

    if scope in _PROVIDER_CACHE:
        return _PROVIDER_CACHE[scope]

    if _IDENTITY_IMPORT_ATTEMPTED and not _IDENTITY_AVAILABLE:
        # A prior call already discovered the dep is missing; don't
        # re-pay the ImportError or re-log the install hint for every
        # additional scope a caller asks about.
        _PROVIDER_CACHE[scope] = None
        return None

    try:
        from azure.identity import (  # type: ignore[import-not-found]
            DefaultAzureCredential,
            get_bearer_token_provider,
        )
    except ImportError:
        log.info(
            "azure-identity is not installed; AAD auth unavailable. "
            "Install with: pip install 'assert-ai[azure-aad]'"
        )
        _IDENTITY_IMPORT_ATTEMPTED = True
        _IDENTITY_AVAILABLE = False
        _PROVIDER_CACHE[scope] = None
        return None

    _IDENTITY_IMPORT_ATTEMPTED = True
    _IDENTITY_AVAILABLE = True

    if _CACHED_CREDENTIAL is None:
        # Skip the interactive flows so CI / non-interactive shells fail
        # fast instead of hanging on a browser prompt.
        _CACHED_CREDENTIAL = DefaultAzureCredential(
            exclude_interactive_browser_credential=True,
            exclude_visual_studio_code_credential=True,
        )
    raw_provider = get_bearer_token_provider(_CACHED_CREDENTIAL, scope)
    provider = _wrap_provider_with_provenance_log(_CACHED_CREDENTIAL, raw_provider)
    _PROVIDER_CACHE[scope] = provider
    return provider


def _wrap_provider_with_provenance_log(
    credential: object,
    raw_provider: Callable[[], str],
) -> Callable[[], str]:
    """Emit one INFO line naming the credential that won the chain.

    azure-identity's ``ChainedTokenCredential`` (which
    ``DefaultAzureCredential`` extends) sets ``_successful_credential``
    after a successful ``get_token``. We read it once after the first
    successful token to give users a single, friendly provenance line
    (``"Azure OpenAI auth: AAD token acquired via AzureCliCredential
    (az login)."``) without flooding logs with the SDK's per-step
    chain trace.

    The underscore-prefixed attribute is internal but has been stable
    across azure-identity versions; we read it defensively with
    ``getattr`` so a future rename degrades to a generic
    "credential chain" label instead of crashing.
    """
    logged: dict[str, bool] = {"done": False}

    def provider() -> str:
        token = raw_provider()
        if not logged["done"]:
            successful = getattr(credential, "_successful_credential", None)
            cls_name = type(successful).__name__ if successful is not None else None
            label = _FRIENDLY_CRED_LABELS.get(cls_name or "", "credential chain")
            if cls_name:
                log.info(
                    "Azure OpenAI auth: AAD token acquired via %s (%s).",
                    cls_name,
                    label,
                )
            else:
                # Token came back but we couldn't identify the inner
                # credential — still tell the user AAD succeeded so
                # they're not left guessing.
                log.info("Azure OpenAI auth: AAD token acquired.")
            logged["done"] = True
        return token

    return provider


# Cached on first access (or after ``refresh_azure_auth_mode(force=True)``).
# Kept module-global so per-request lookups stay free (no env reads,
# no extra function calls in the hot path) once warmed.
#
# Resolution is deliberately *lazy*: process entrypoints that load
# ``.env`` (the runner, ``assert-ai init``) call
# ``refresh_azure_auth_mode(force=True)`` after ``load_dotenv`` so the
# resolved mode reflects the dotenv-populated environment, not just
# the shell vars present at module import.
_AZURE_AUTH_MODE: Mode | None = None

# True when the user's environment selects AAD (explicit or fallback)
# but the ``azure-identity`` package is not importable. Used by
# ``model_client._classify_llm_error`` to swap the RBAC hint for an
# install hint. Updated alongside ``_AZURE_AUTH_MODE`` whenever the
# cache is refreshed.
_AZURE_AAD_DEP_MISSING: bool = False


def refresh_azure_auth_mode(force: bool = False) -> Mode:
    """Resolve the Azure auth mode from the current environment and cache it.

    Idempotent: once the cache is populated, subsequent calls are no-ops
    unless ``force=True``. Entrypoints that load ``.env`` should call this
    with ``force=True`` immediately after ``load_dotenv`` so the resolved
    mode reflects the dotenv-populated environment.

    Returns the resolved mode for the caller's convenience.
    """
    global _AZURE_AUTH_MODE, _AZURE_AAD_DEP_MISSING
    if _AZURE_AUTH_MODE is not None and not force:
        return _AZURE_AUTH_MODE
    _AZURE_AUTH_MODE = resolve_azure_auth_mode()
    _AZURE_AAD_DEP_MISSING = (
        _AZURE_AUTH_MODE in ("aad", "aad-fallback")
        and get_azure_token_provider() is None
    )
    return _AZURE_AUTH_MODE


def _get_azure_auth_mode() -> Mode:
    """Return the cached auth mode, resolving lazily on first access."""
    if _AZURE_AUTH_MODE is None:
        return refresh_azure_auth_mode()
    return _AZURE_AUTH_MODE


def log_resolved_azure_auth_mode() -> None:
    """Emit a single INFO/WARNING line describing the active Azure auth mode.

    Safe to call multiple times — it always logs the currently-resolved
    state. Intended to be called by entrypoints (CLI, library hosts) once
    their logging is configured, so users have a reliable startup anchor
    for which auth path will be used by ``azure/*`` requests. A followup
    provenance line ("AAD token acquired via …") fires on the first
    successful token acquisition, but is easy to miss mid-stream without
    this anchor.
    """
    mode = _get_azure_auth_mode()
    if mode == "aad":
        log.info(
            "Azure OpenAI auth mode: AAD (forced via %s).",
            ENV_USE_AAD_FLAG,
        )
        if _AZURE_AAD_DEP_MISSING:
            log.warning(
                "%s is set but azure-identity is not installed; the next "
                "azure/* request will fail. Install with: "
                "pip install 'assert-ai[azure-aad]'",
                ENV_USE_AAD_FLAG,
            )
    elif mode == "aad-fallback":
        if _AZURE_AAD_DEP_MISSING:
            log.info(
                "Azure OpenAI auth mode: AAD fallback (no AZURE_API_KEY set; "
                "azure-identity not installed — install with: "
                "pip install 'assert-ai[azure-aad]').",
            )
        else:
            log.info(
                "Azure OpenAI auth mode: AAD fallback (no AZURE_API_KEY set; "
                "using DefaultAzureCredential).",
            )
    else:
        log.info("Azure OpenAI auth mode: API key (AZURE_API_KEY).")


def _reset_cache_for_tests() -> None:
    """Clear the cached providers and credential — for tests only."""
    global _CACHED_CREDENTIAL, _IDENTITY_IMPORT_ATTEMPTED, _IDENTITY_AVAILABLE
    _PROVIDER_CACHE.clear()
    _CACHED_CREDENTIAL = None
    _IDENTITY_IMPORT_ATTEMPTED = False
    _IDENTITY_AVAILABLE = False


def _reset_auth_mode_cache_for_tests() -> None:
    """Clear the cached auth-mode resolution — for tests only.

    Resets both ``_AZURE_AUTH_MODE`` and ``_AZURE_AAD_DEP_MISSING`` to
    their pre-resolution baseline so the next ``refresh_azure_auth_mode``
    or ``_get_azure_auth_mode`` call resolves against the current env.
    Centralises the field list so future additions to the auth-mode
    cache do not require touching every test that needs a clean slate.
    """
    global _AZURE_AUTH_MODE, _AZURE_AAD_DEP_MISSING
    _AZURE_AUTH_MODE = None
    _AZURE_AAD_DEP_MISSING = False
