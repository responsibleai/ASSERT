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
    _CACHED_PROVIDER = get_bearer_token_provider(credential, AZURE_OPENAI_SCOPE)
    _CACHE_POPULATED = True
    return _CACHED_PROVIDER


def _reset_cache_for_tests() -> None:
    """Clear the cached provider — for tests only."""
    global _CACHED_PROVIDER, _CACHE_POPULATED
    _CACHED_PROVIDER = None
    _CACHE_POPULATED = False
