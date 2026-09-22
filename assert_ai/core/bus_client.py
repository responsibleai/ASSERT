# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Optional BUS transport adapter for ASSERT model calls."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from assert_ai.core.config_model import BusConfig
from assert_ai.core.token_message_client import TokenMessageCompletion as BusCompletion, complete_messages


def _load_bus_stack() -> tuple[Any, Any, Any, Callable[[Any], str], Any]:
    try:
        from bus.qos_type import QoSType
        from bus_token_completer import BusTokenCompleter
        from chat import chat
        from chat.render.common import render_content
        from message_completer.token_message_completer import TokenMessageCompleter
    except ImportError as exc:
        raise RuntimeError(
            "BUS model transport requires the bus, bus_token_completer, chat, "
            "and message_completer packages to be available on PYTHONPATH"
        ) from exc
    return QoSType, BusTokenCompleter, chat, render_content, TokenMessageCompleter


async def complete(
    messages: Sequence[Mapping[str, Any]],
    *,
    config: BusConfig,
    max_tokens: int | None,
    temperature: float | None,
) -> BusCompletion:
    """Send one conversation through ``BusTokenCompleter``."""
    QoSType, BusTokenCompleter, chat, render_content, TokenMessageCompleter = _load_bus_stack()
    token_completer_config = BusTokenCompleter.Config(
        topic_or_snapshot=config.snapshot,
        topic_mode_or_user=config.user,
        bus_line=config.bus_line,
        qos_type=QoSType.ROUND_ROBIN_BY_USER,
    )
    return await complete_messages(
        messages,
        token_completer_config=token_completer_config,
        renderer=config.renderer,
        top_p=config.top_p,
        max_tokens=max_tokens,
        temperature=temperature,
        transport="BUS",
        chat=chat,
        render_content=render_content,
        message_completer_type=TokenMessageCompleter,
    )