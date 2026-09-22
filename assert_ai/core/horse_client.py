# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Optional Horse transport adapter for ASSERT model calls."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from assert_ai.core.config_model import HorseConfig
from assert_ai.core.token_message_client import TokenMessageCompletion as HorseCompletion, complete_messages


def _load_horse_stack() -> tuple[Any, Any, Callable[[Any], str], Any]:
    try:
        from ev3_token_completer import Ev3TokenCompleter
        from chat import chat
        from chat.render.common import render_content
        from message_completer.token_message_completer import TokenMessageCompleter
    except ImportError as exc:
        raise RuntimeError(
            "Horse model transport requires the ev3_token_completer, chat, "
            "and message_completer packages to be available on PYTHONPATH"
        ) from exc
    return Ev3TokenCompleter, chat, render_content, TokenMessageCompleter


async def complete(
    messages: Sequence[Mapping[str, Any]],
    *,
    config: HorseConfig,
    max_tokens: int | None,
    temperature: float | None,
) -> HorseCompletion:
    """Send one conversation through ``Ev3TokenCompleter`` in Horse mode."""
    Ev3TokenCompleter, chat, render_content, TokenMessageCompleter = _load_horse_stack()
    token_completer_config = Ev3TokenCompleter.Config(
        use_horse=True,
        model_name=config.model_name,
    )
    return await complete_messages(
        messages,
        token_completer_config=token_completer_config,
        renderer=config.renderer,
        top_p=config.top_p,
        max_tokens=max_tokens,
        temperature=temperature,
        transport="Horse",
        chat=chat,
        render_content=render_content,
        message_completer_type=TokenMessageCompleter,
    )
