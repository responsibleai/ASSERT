"""Hosted and external session execution paths for eval targets."""

from __future__ import annotations

import importlib
import inspect
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from p2m.core.async_utils import invoke_callable
from p2m.core.model_client import (
    GenerateOptions,
    Message,
    ModelResponse,
    build_llm_call_trace,
    generate,
    generate_with_tools,
    summarize_response,
)
from p2m.core.tool_backend import load_tool_module
from p2m.core.tools import build_target_tools


# ── Adapter types and helpers ──────────────────────────────────

@dataclass
class AdapterEvent:
    role: Literal["assistant", "tool_call", "tool_result"]
    content: str
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_call_id: str | None = None


@dataclass
class ConnectorResponse:
    text: str
    events: list[AdapterEvent] | None = None
    raw: dict[str, Any] | None = None


def _discover_connector_class(module: Any) -> type[Any]:
    named = getattr(module, "Adapter", None)
    if inspect.isclass(named):
        return named

    classes = [
        member
        for _, member in inspect.getmembers(module, inspect.isclass)
        if member.__module__ == module.__name__
    ]
    for cls in classes:
        try:
            signature = inspect.signature(cls.__init__)
        except (TypeError, ValueError):
            continue
        params = list(signature.parameters.values())[1:]
        if params and params[0].name == "scenario":
            return cls
    class_names = ", ".join(sorted(cls.__name__ for cls in classes)) or "(none)"
    raise ValueError(
        f"Could not find an Adapter class in module '{module.__name__}'. Found classes: {class_names}",
    )


def _normalize_connector_response(raw: Any) -> ConnectorResponse:
    if isinstance(raw, str):
        return ConnectorResponse(text=raw)

    raw_payload: dict[str, Any] | None = None
    text: str
    events_raw: Any = None

    if isinstance(raw, ConnectorResponse):
        text = raw.text
        events_raw = raw.events
        raw_payload = raw.raw if isinstance(raw.raw, dict) else None
    elif isinstance(raw, dict):
        text = raw.get("text")
        if not isinstance(text, str):
            text = raw.get("content")
        if not isinstance(text, str):
            text = str(text or "")
        events_raw = raw.get("events")
        raw_payload = raw.get("raw") if isinstance(raw.get("raw"), dict) else dict(raw)
    else:
        return ConnectorResponse(text=str(raw))

    events: list[AdapterEvent] | None = None
    if isinstance(events_raw, list):
        events = []
        for event in events_raw:
            if not isinstance(event, dict):
                continue
            role = event.get("role")
            if role not in {"assistant", "tool_call", "tool_result"}:
                continue
            events.append(
                AdapterEvent(
                    role=role,
                    content=str(event.get("content") or ""),
                    tool_name=event.get("tool_name"),
                    tool_args=event.get("tool_args") if isinstance(event.get("tool_args"), dict) else None,
                    tool_call_id=event.get("tool_call_id"),
                )
            )

    return ConnectorResponse(text=text, events=events, raw=raw_payload)


# ── Session types ──────────────────────────────────────────────
@dataclass
class ResolverContext:
    conversation_messages: list[Message]
    tool_history: list[dict[str, Any]]


@dataclass
class ToolResolution:
    output: str
    raw: dict[str, Any] | None = None
    llm_call: dict[str, Any] | None = None


@dataclass
class ToolTrace:
    tool_name: str
    tool_args: dict[str, Any]
    tool_result: str
    raw: dict[str, Any] | None = None
    llm_call: dict[str, Any] | None = None


@dataclass
class TurnResult:
    text: str
    state_messages: list[Message]
    interaction_messages: list[dict[str, Any]]
    tool_traces: list[ToolTrace] = field(default_factory=list)
    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] | None = None


class SimulatedResolver:
    def __init__(
        self,
        *,
        model: str,
        prompt_template: str,
        scenario: dict[str, Any],
        timeout_s: float | None = None,
    ) -> None:
        self._model = model
        self._prompt_template = prompt_template
        self._scenario = scenario
        self._timeout_s = timeout_s

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def resolve(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        context: ResolverContext,
    ) -> ToolResolution:
        prompt = self._prompt_template
        replacements = {
            "{{description}}": str(self._scenario.get("description") or ""),
            "{{tool_name}}": tool_name,
            "{{tool_args}}": json.dumps(tool_args, ensure_ascii=False),
            "{{conversation}}": _render_conversation(context.conversation_messages),
            "{{tool_history}}": _render_tool_history(context.tool_history),
        }
        for placeholder, value in replacements.items():
            prompt = prompt.replace(placeholder, value)
        response = await generate(
            self._model,
            prompt,
            options=GenerateOptions(timeout_s=self._timeout_s),
        )
        return ToolResolution(
            output=response.text or "",
            raw={
                "call": "simulated",
                "request": {"prompt": prompt},
                "response": summarize_response(response),
            },
            llm_call=build_llm_call_trace(response, source="tool_simulator"),
        )


class HostedSession:
    def __init__(
        self,
        *,
        model: str,
        generate_options: GenerateOptions,
        tools: list[dict[str, Any]] | None = None,
        resolver: Any = None,
        max_tool_calls: int = 10,
        runtime_label: str = "chat",
    ) -> None:
        self._model = model
        self._generate_options = generate_options
        self._tools = build_target_tools(tools) if tools else []
        self._resolver = resolver
        self._max_tool_calls = max_tool_calls
        self._runtime_label = runtime_label
        self._tool_history: list[dict[str, Any]] = []
        self._is_open = False
        self._needs_close = False

    @property
    def runtime_mode(self) -> str:
        return self._runtime_label

    @property
    def session_metadata(self) -> dict[str, Any] | None:
        session_info = getattr(self._resolver, "session_metadata", None)
        if callable(session_info):
            metadata = session_info()
            return metadata if isinstance(metadata, dict) else None
        return None

    async def open(self) -> None:
        if self._is_open:
            return
        if self._resolver is not None:
            self._needs_close = True
            await self._resolver.open()
        self._is_open = True

    async def close(self) -> None:
        if not self._is_open and not self._needs_close:
            return
        try:
            if self._resolver is not None:
                await self._resolver.close()
        finally:
            self._tool_history = []
            self._is_open = False
            self._needs_close = False

    async def _invoke_model(self, messages: list[Message]) -> ModelResponse:
        if not self._tools or self._resolver is None:
            return await generate(self._model, messages, options=self._generate_options)
        return await generate_with_tools(
            self._model,
            messages,
            tools=self._tools,
            options=self._generate_options,
        )

    async def run_turn(self, messages: list[Message]) -> TurnResult:
        await self.open()
        interaction_messages: list[dict[str, Any]] = []
        llm_calls: list[dict[str, Any]] = []
        latest_user_message = _latest_user_message(messages)
        if latest_user_message is not None:
            interaction_messages.append(_serialize_message(latest_user_message))
        if not self._tools or self._resolver is None:
            response = await self._invoke_model(messages)
            llm_call_index = len(llm_calls)
            llm_calls.append(build_llm_call_trace(response, source="target"))
            interaction_messages.append(
                _serialize_final_assistant_message(
                    response,
                    session=self.session_metadata,
                    llm_call_index=llm_call_index,
                )
            )
            return TurnResult(
                text=response.text or "",
                state_messages=list(messages) + [response.message],
                interaction_messages=interaction_messages,
                llm_calls=llm_calls,
                raw=_session_raw_payload(response, self.session_metadata),
            )

        working_messages = list(messages)
        tool_traces: list[ToolTrace] = []
        response = await self._invoke_model(working_messages)
        tool_call_count = 0
        while response.tool_calls and tool_call_count < self._max_tool_calls:
            working_messages.append(response.message)
            llm_call_index = len(llm_calls)
            llm_calls.append(build_llm_call_trace(response, source="target"))
            interaction_messages.append(
                _serialize_assistant_tool_call_message(
                    response,
                    session=self.session_metadata,
                    llm_call_index=llm_call_index,
                )
            )
            for tool_call in response.tool_calls:
                if tool_call_count >= self._max_tool_calls:
                    break
                tool_args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
                resolution = await self._resolver.resolve(
                    tool_name=tool_call.name,
                    tool_args=tool_args,
                    context=ResolverContext(
                        conversation_messages=list(working_messages),
                        tool_history=list(self._tool_history),
                    ),
                )
                tool_call_count += 1
                self._tool_history.append(
                    _tool_history_entry(tool_call.name, tool_args, resolution.output)
                )
                tool_traces.append(
                    ToolTrace(
                        tool_name=tool_call.name,
                        tool_args=tool_args,
                        tool_result=resolution.output,
                        raw=resolution.raw,
                        llm_call=resolution.llm_call,
                    )
                )
                tool_result_payload = {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "function": tool_call.name,
                    "arguments": tool_args,
                    "content": resolution.output,
                    "raw": resolution.raw,
                }
                if resolution.llm_call is not None:
                    llm_call_index = len(llm_calls)
                    llm_calls.append(resolution.llm_call)
                    tool_result_payload["llm_call_index"] = llm_call_index
                working_messages.append(
                    Message(role="tool", content=resolution.output, tool_call_id=tool_call.id),
                )
                interaction_messages.append(tool_result_payload)
            response = await self._invoke_model(working_messages)

        if response.tool_calls and tool_call_count >= self._max_tool_calls:
            working_messages.append(response.message)
            llm_call_index = len(llm_calls)
            llm_calls.append(build_llm_call_trace(response, source="target"))
            interaction_messages.append(
                _serialize_assistant_tool_call_message(
                    response,
                    session=self.session_metadata,
                    llm_call_index=llm_call_index,
                )
            )
            for tool_call in response.tool_calls:
                tool_args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
                working_messages.append(
                    Message(role="tool", content="Tool call limit reached.", tool_call_id=tool_call.id),
                )
                interaction_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "function": tool_call.name,
                        "arguments": tool_args,
                        "content": "Tool call limit reached.",
                        "raw": {
                            "call": "tool_limit",
                            "max_tool_calls": self._max_tool_calls,
                        },
                    }
                )
            response = await generate(self._model, working_messages, options=self._generate_options)

        interaction_messages.append(
            _serialize_final_assistant_message(
                response,
                session=self.session_metadata,
                llm_call_index=len(llm_calls),
            )
        )
        llm_calls.append(build_llm_call_trace(response, source="target"))
        raw = _session_raw_payload(response, self.session_metadata)
        return TurnResult(
            text=response.text or "",
            state_messages=working_messages + [response.message],
            interaction_messages=interaction_messages,
            tool_traces=tool_traces,
            llm_calls=llm_calls,
            raw=raw,
        )


class CallableSession:
    """Invokes a user-provided callable as the eval target.

    Supports sync and async callables:
    - fn(str) -> str
    - fn(str, history=list) -> str
    """

    def __init__(
        self,
        *,
        callable_ref: str,
        system_prompt: str | None = None,
        message_timeout_s: float | None = None,
    ) -> None:
        self._callable_ref = callable_ref
        self._system_prompt = system_prompt
        self._message_timeout_s = message_timeout_s
        self._callable = None
        self._supports_history = False

    @property
    def runtime_mode(self) -> str:
        return "callable"

    async def open(self) -> None:
        module_path, func_name = self._callable_ref.rsplit(":", 1)
        mod = importlib.import_module(module_path)
        self._callable = getattr(mod, func_name)
        sig = inspect.signature(self._callable)
        self._supports_history = "history" in sig.parameters

    async def close(self) -> None:
        self._callable = None

    async def run_turn(self, messages: list[Message]) -> TurnResult:
        user_text = ""
        for msg in reversed(messages):
            if msg.role == "user":
                user_text = msg.text
                break

        if self._supports_history:
            history = [
                {"role": msg.role, "content": msg.text}
                for msg in messages
                if msg.role in ("user", "assistant")
            ]
            response_text = await invoke_callable(
                self._callable, user_text, history=history,
                timeout_s=self._message_timeout_s,
            )
        else:
            response_text = await invoke_callable(
                self._callable, user_text,
                timeout_s=self._message_timeout_s,
            )

        if not isinstance(response_text, str):
            response_text = str(response_text)

        interaction_messages = [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": response_text},
        ]

        return TurnResult(
            text=response_text,
            state_messages=list(messages) + [Message(role="assistant", content=response_text)],
            interaction_messages=interaction_messages,
            raw={"callable": self._callable_ref},
        )


class ExternalSession:
    def __init__(
        self,
        *,
        connector_ref: str,
        scenario: dict[str, Any],
        startup_timeout_s: float | None = None,
        message_timeout_s: float | None = None,
        config_path: Path | None = None,
    ) -> None:
        connector_cls = _discover_connector_class(load_tool_module(connector_ref, config_path=config_path))
        self._startup_timeout_s = startup_timeout_s
        self._message_timeout_s = message_timeout_s
        self._connector = connector_cls(scenario)
        self._is_open = False

    @property
    def runtime_mode(self) -> str:
        return "external"

    async def open(self) -> None:
        if self._is_open:
            return
        open_fn = getattr(self._connector, "open", None)
        if callable(open_fn):
            await invoke_callable(open_fn, timeout_s=self._startup_timeout_s)
        self._is_open = True

    async def close(self) -> None:
        if not self._is_open:
            return
        close_fn = getattr(self._connector, "close", None)
        if callable(close_fn):
            await invoke_callable(close_fn, timeout_s=self._startup_timeout_s)
        self._is_open = False

    async def run_turn(self, messages: list[Message]) -> TurnResult:
        await self.open()
        history = [_serialize_message(message) for message in messages]
        latest_user_message = _latest_user_message(messages)
        user_text = latest_user_message.text if latest_user_message is not None else ""
        response = _normalize_connector_response(
            await invoke_callable(
                self._connector.send_message,
                user_text,
                history=history,
                timeout_s=self._message_timeout_s,
            )
        )
        interaction_messages = _serialize_connector_interaction_messages(
            user_text=user_text,
            response=response,
        )
        state_messages = list(messages) + [Message(role="assistant", content=response.text)]
        return TurnResult(
            text=response.text,
            state_messages=state_messages,
            interaction_messages=interaction_messages,
            raw=response.raw,
        )



def _serialize_message(message: Message) -> dict[str, Any]:
    payload = {"role": message.role, "content": message.text or "", "raw": {"message": message.to_openai_dict()}}
    if message.tool_calls:
        payload["tool_calls"] = [_serialize_tool_call(tool_call) for tool_call in message.tool_calls]
    if message.tool_call_id:
        payload["tool_call_id"] = message.tool_call_id
    return payload


def _latest_user_message(messages: list[Message]) -> Message | None:
    for message in reversed(messages):
        if message.role == "user":
            return message
    return None


def _serialize_tool_call(tool_call: Any) -> dict[str, Any]:
    return {
        "id": tool_call.id,
        "function": tool_call.function,
        "arguments": tool_call.arguments,
    }


def _tool_history_entry(tool_name: str, tool_args: dict[str, Any], tool_result: str) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "tool_args": tool_args,
        "tool_result": tool_result,
    }


def serialize_response(response: ModelResponse) -> dict[str, Any]:
    return summarize_response(response)


def _session_raw_payload(
    response: ModelResponse,
    session: dict[str, Any] | None,
) -> dict[str, Any]:
    raw = {
        "response": serialize_response(response),
    }
    if session:
        raw["session"] = session
    return raw


def _serialize_assistant_tool_call_message(
    response: ModelResponse,
    *,
    session: dict[str, Any] | None = None,
    llm_call_index: int | None = None,
) -> dict[str, Any]:
    message = response.message
    payload = {
        "role": "assistant",
        "content": message.text or "",
        "tool_calls": [_serialize_tool_call(tool_call) for tool_call in message.tool_calls],
        "raw": {
            "message": message.to_openai_dict(),
            **_session_raw_payload(response, session),
        },
    }
    if llm_call_index is not None:
        payload["llm_call_index"] = llm_call_index
    return payload


def _serialize_final_assistant_message(
    response: ModelResponse,
    *,
    session: dict[str, Any] | None = None,
    llm_call_index: int | None = None,
) -> dict[str, Any]:
    message = response.message
    payload = {
        "role": "assistant",
        "content": response.text or "",
        "raw": {
            "message": message.to_openai_dict(),
            **_session_raw_payload(response, session),
        },
    }
    if llm_call_index is not None:
        payload["llm_call_index"] = llm_call_index
    return payload


def _render_conversation(messages: list[Message]) -> str:
    lines: list[str] = []
    for message in messages:
        if message.role in {"system", "tool"}:
            continue
        if message.role == "assistant" and not message.text.strip():
            continue
        label = "User" if message.role == "user" else "Target"
        lines.append(f"{label}: {message.text}")
    return "\n".join(lines) if lines else "(no messages yet)"


def _render_tool_history(tool_history: list[dict[str, Any]]) -> str:
    if not tool_history:
        return "(none yet)"
    return "\n".join(
        f"- {entry['tool_name']}({json.dumps(entry['tool_args'], ensure_ascii=False)}) -> {entry['tool_result']}"
        for entry in tool_history
    )


def _serialize_connector_interaction_messages(
    *,
    user_text: str,
    response: Any,
) -> list[dict[str, Any]]:
    messages = [
        {
            "role": "user",
            "content": user_text,
            "raw": {"message": {"role": "user", "content": user_text}},
        }
    ]
    if response.events:
        for event in response.events:
            if event.role == "assistant":
                messages.append({"role": "assistant", "content": event.content, "raw": response.raw})
            elif event.role == "tool_call":
                messages.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": event.tool_call_id,
                                "function": event.tool_name or "tool",
                                "arguments": event.tool_args or {},
                            }
                        ],
                        "raw": response.raw,
                    }
                )
            elif event.role == "tool_result":
                messages.append(
                    {
                        "role": "tool",
                        "content": event.content,
                        "function": event.tool_name or "tool",
                        "arguments": event.tool_args or {},
                        "tool_call_id": event.tool_call_id,
                        "raw": response.raw,
                    }
                )
        return messages

    messages.append({"role": "assistant", "content": response.text, "raw": response.raw})
    return messages
