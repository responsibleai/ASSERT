#!/usr/bin/env python3
"""Thin wrapper that patches litellm for per-model endpoint routing, then runs p2m.

p2m's pipeline uses multiple models (systematize, test_set, judge) that may live on
a different Azure OpenAI endpoint than the inference target model.  Since litellm
resolves all ``azure/*`` models via a single ``AZURE_API_BASE`` env var, target
models on different endpoints need per-call ``api_base`` / ``api_key`` injection.

The routing table is passed via the ``_P2M_MODEL_ROUTING`` environment variable
as a JSON object::

    {
        "azure/gpt-oss-120b": {
            "api_base": "https://westus2.openai.azure.com/",
            "api_key": "..."
        }
    }

Models NOT in the routing table use the default ``AZURE_API_BASE`` / ``AZURE_API_KEY``
environment variables — which is the correct behavior for pipeline models
(systematize, test_set, tester, judge) that are deployed on the default endpoint.
"""
from __future__ import annotations

import json
import os
import sys


def _install_routing_hook() -> None:
    # Pop the routing env var so it doesn't leak into child processes.
    routing_json = os.environ.pop("_P2M_MODEL_ROUTING", "")
    if not routing_json:
        return

    routing: dict[str, dict[str, str]] = json.loads(routing_json)
    if not routing:
        return

    import litellm

    _orig_acompletion = litellm.acompletion

    async def _routed_acompletion(*args, **kwargs):  # type: ignore[no-untyped-def]
        model = kwargs.get("model") or (args[0] if args else "")
        overrides = routing.get(model, {})
        for key in ("api_base", "api_key"):
            val = overrides.get(key)
            if val and key not in kwargs:
                kwargs[key] = val
        return await _orig_acompletion(*args, **kwargs)

    litellm.acompletion = _routed_acompletion  # type: ignore[assignment]


_install_routing_hook()

# Hand off to p2m CLI.
sys.argv[0] = "p2m"
from p2m.cli import cli  # noqa: E402

cli()
