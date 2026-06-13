# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Optional Entra ID / Managed Identity auth for Azure OpenAI calls.

This module is a leaf helper used by ``assert_ai.core.model_client`` to
inject an ``azure_ad_token_provider`` into LiteLLM payloads for
``azure/*`` models. It is strictly additive: when the user has set
``AZURE_API_KEY`` (and not opted into AAD), the legacy key-based path
runs unchanged.

Auth-mode precedence (least surprise — key first):

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

#: Env var users set to force AAD even when ``AZURE_API_KEY`` is present.
ENV_USE_AAD_FLAG = "ASSERT_AZURE_USE_AAD"

_TRUTHY = {"1", "true", "yes", "on"}

# Maps the azure-identity credential class name to a short, friendly
# label so users see "az login" instead of "AzureCliCredential" in the
# one-line provenance log. Anything not in this map falls back to the
# raw class name — better than silence, and unlikely in practice.
_FRIENDLY_CRED_LABELS: Mapping[str, str] = {
    "AzureCliCredential": "az login",
    "AzureDeveloperCliCredential": "azd login",
    "AzurePowerShellCredential": "Azure PowerShell",
    "ManagedIdentityCredential": "managed identity",
    "WorkloadIdentityCredential": "workload identity",
    "EnvironmentCredential": "service principal (env vars)",
    "SharedTokenCacheCredential": "shared token cache",
    "VisualStudioCodeCredential": "VS Code",
}

# DefaultAzureCredential is expensive to construct repeatedly and
# azure-identity already handles its own in-process token caching, so
# the provider callable is built lazily once and reused for the
# process lifetime.
_CACHED_PROVIDER: Callable[[], str] | None = None
_CACHE_POPULATED = False


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


def get_azure_token_provider() -> Callable[[], str] | None:
    """Return a cached bearer-token callable for Azure OpenAI, or ``None``.

    Returns ``None`` (without raising) when ``azure-identity`` is not
    installed. Callers should treat ``None`` as "AAD requested but
    optional dependency missing" and surface an actionable install hint.

    Honors ``AZURE_CLIENT_ID`` natively via ``DefaultAzureCredential``
    so users can select a specific user-assigned managed identity
    without passing anything to ASSERT.
    """
    global _CACHED_PROVIDER, _CACHE_POPULATED
    if _CACHE_POPULATED:
        return _CACHED_PROVIDER

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
        _CACHE_POPULATED = True
        return None

    # Skip the interactive flows so CI / non-interactive shells fail
    # fast instead of hanging on a browser prompt.
    credential = DefaultAzureCredential(
        exclude_interactive_browser_credential=True,
        exclude_visual_studio_code_credential=True,
    )
    raw_provider = get_bearer_token_provider(credential, AZURE_OPENAI_SCOPE)
    _CACHED_PROVIDER = _wrap_provider_with_provenance_log(credential, raw_provider)
    _CACHE_POPULATED = True
    return _CACHED_PROVIDER


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


def _reset_cache_for_tests() -> None:
    """Clear the cached provider — for tests only."""
    global _CACHED_PROVIDER, _CACHE_POPULATED
    _CACHED_PROVIDER = None
    _CACHE_POPULATED = False
