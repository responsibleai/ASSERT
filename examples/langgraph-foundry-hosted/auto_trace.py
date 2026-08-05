# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Callable wrapper that evaluates the live Foundry-hosted agent via the
OpenAI Responses protocol endpoint.

Used by eval_config.yaml as:
    target:
      callable: auto_trace:chat_sync
"""

from __future__ import annotations

import os

import httpx
from azure.identity import DefaultAzureCredential

_AGENT_ENDPOINT = os.environ.get("FOUNDRY_AGENT_ENDPOINT")
_AZURE_AI_SCOPE = "https://ai.azure.com/.default"


def chat_sync(message: str, history: list[dict] | None = None) -> str:
    """Call the live Foundry-hosted agent and return its text response.

    assert-ai passes ``history`` as OpenAI-format messages on multi-turn runs.
    """
    if not _AGENT_ENDPOINT:
        raise RuntimeError(
            "FOUNDRY_AGENT_ENDPOINT is not set. Add it to your .env (the deployed "
            "agent's Responses endpoint printed by `azd deploy`)."
        )
    credential = DefaultAzureCredential()
    token = credential.get_token(_AZURE_AI_SCOPE).token

    input_messages: list[dict] | str
    if history:
        input_messages = list(history)
        if not input_messages or input_messages[-1].get("content") != message:
            input_messages.append({"role": "user", "content": message})
    else:
        input_messages = message

    resp = httpx.post(
        _AGENT_ENDPOINT,
        params={"api-version": "v1"},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"input": input_messages},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()

    # The graph emits intermediate assistant messages (e.g. the intent
    # classifier's routing JSON) before the final answer, so walk the output
    # array in REVERSE and return the last assistant text message.
    for item in reversed(data.get("output", [])):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    return part["text"]
    # output_text is a convenience property on the Responses object
    if "output_text" in data:
        return data["output_text"]
    return str(data)
