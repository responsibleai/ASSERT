"""
Event-based transcript for multi-turn audits.

Supports append-only event log with multiple views.
"""

import json
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

ROLE_LABEL_BY_ROLE = {"user": "User", "assistant": "Assistant", "system": "System", "tool": "Tool"}
MESSAGE_ROLES = set(ROLE_LABEL_BY_ROLE.keys())

_DEFAULT_MAX_MESSAGE_CHARS = 10_000


class EditType(str, Enum):
    ADD_MESSAGE = "add_message"
    SET_SYSTEM_MESSAGE = "set_system_message"
    TOOL_CALL = "tool_call"


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class AddMessageEdit(BaseModel):
    type: Literal["add_message"] = "add_message"
    message: Message


class SetSystemMessageEdit(BaseModel):
    type: Literal["set_system_message"] = "set_system_message"
    message: Message


class ToolCallEdit(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    tool_name: str
    tool_args: Dict[str, Any] = Field(default_factory=dict)
    tool_result: str = ""


Edit = Union[AddMessageEdit, SetSystemMessageEdit, ToolCallEdit]


@dataclass(frozen=True)
class SearchableMessageEntry:
    message_id: str
    message: "Message"
    tool_name: str | None = None
    tool_args: Dict[str, Any] = field(default_factory=dict)
    tool_result: str = ""
    tool_call_id: str | None = None


def _event_views(value: Any) -> List[str]:
    """Normalize event view payloads from either model or dict forms."""
    if isinstance(value, list):
        return [str(v) for v in value]
    if value is None:
        return []
    return [str(value)]


def _edit_type(edit: Any) -> Optional[str]:
    """Read an edit type without relying on Python module identity."""
    if isinstance(edit, dict):
        value = edit.get("type")
    else:
        value = getattr(edit, "type", None)
    return str(value) if value is not None else None


def _message_from_payload(payload: Any) -> Optional["Message"]:
    """Coerce dict/model payloads into the local Message type."""
    if isinstance(payload, Message):
        return payload

    role = getattr(payload, "role", None)
    content = getattr(payload, "content", None)
    if isinstance(payload, dict):
        role = payload.get("role")
        content = payload.get("content")

    if role not in MESSAGE_ROLES or not isinstance(content, str):
        return None

    return Message(role=role, content=content)


def _message_from_edit(edit: Any) -> Optional["Message"]:
    """Materialize a display message from a typed edit or a serialized dict."""
    kind = _edit_type(edit)
    if kind in (EditType.ADD_MESSAGE.value, EditType.SET_SYSTEM_MESSAGE.value):
        payload = edit.get("message") if isinstance(edit, dict) else getattr(edit, "message", None)
        return _message_from_payload(payload)

    if kind == EditType.TOOL_CALL.value:
        tool_name = edit.get("tool_name", "") if isinstance(edit, dict) else getattr(edit, "tool_name", "")
        tool_args = edit.get("tool_args", {}) if isinstance(edit, dict) else getattr(edit, "tool_args", {})
        tool_result = edit.get("tool_result", "") if isinstance(edit, dict) else getattr(edit, "tool_result", "")
        content = _format_tool_call_content(tool_name, tool_args, tool_result)
        return Message(role="tool", content=content)

    return None


def _format_tool_call_content(tool_name: str, tool_args: Dict[str, Any], tool_result: str) -> str:
    """Render tool-call content consistently across judge and viewer paths."""
    return f"[Tool call: {tool_name}({json.dumps(tool_args, ensure_ascii=False)}) → {tool_result}]"


def _tool_call_id_from_raw(raw: Any, tool_name: str, tool_args: Dict[str, Any]) -> str | None:
    if not isinstance(raw, dict):
        return None

    direct_id = raw.get("tool_call_id")
    if isinstance(direct_id, str) and direct_id:
        return direct_id

    tool_calls = raw.get("tool_calls")
    if not isinstance(tool_calls, list) or not tool_calls:
        return None

    matching_ids: list[str] = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        raw_id = tool_call.get("id")
        raw_name = tool_call.get("function") or tool_call.get("name")
        raw_args = tool_call.get("arguments") if isinstance(tool_call.get("arguments"), dict) else {}
        if str(raw_name or "") != tool_name:
            continue
        if raw_args != tool_args:
            continue
        if isinstance(raw_id, str) and raw_id:
            matching_ids.append(raw_id)

    unique_ids = list(dict.fromkeys(matching_ids))
    if len(unique_ids) == 1:
        return unique_ids[0]

    if len(tool_calls) == 1 and isinstance(tool_calls[0], dict):
        only_id = tool_calls[0].get("id")
        if isinstance(only_id, str) and only_id:
            return only_id
    return None


def _message_id_for_event_index(event_index: int) -> str:
    """Derive a stable synthetic message ID from transcript event order."""
    return f"event:{event_index}"


def _escape_xml_text(text: str) -> str:
    """Escape XML special characters in text nodes."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _collect_searchable_message_entries_for_view(events: List[Any], view: str) -> List[SearchableMessageEntry]:
    """Collect view-specific searchable message entries from typed events or raw dict events."""
    entries: List[SearchableMessageEntry] = []
    for event_index, event in enumerate(events):
        views = _event_views(event.get("view") if isinstance(event, dict) else getattr(event, "view", None))
        if view not in views:
            continue

        edit = event.get("edit") if isinstance(event, dict) else getattr(event, "edit", None)
        raw = event.get("raw") if isinstance(event, dict) else getattr(event, "raw", None)
        kind = _edit_type(edit)
        message_id = _message_id_for_event_index(event_index)

        if kind in (EditType.ADD_MESSAGE.value, EditType.SET_SYSTEM_MESSAGE.value):
            message = _message_from_edit(edit)
            if message is not None:
                entries.append(SearchableMessageEntry(message_id=message_id, message=message))
            continue

        if kind != EditType.TOOL_CALL.value:
            continue

        tool_name = edit.get("tool_name", "") if isinstance(edit, dict) else getattr(edit, "tool_name", "")
        tool_args = edit.get("tool_args", {}) if isinstance(edit, dict) else getattr(edit, "tool_args", {})
        tool_result = edit.get("tool_result", "") if isinstance(edit, dict) else getattr(edit, "tool_result", "")
        if not isinstance(tool_name, str):
            continue
        if not isinstance(tool_args, dict):
            tool_args = {}
        if not isinstance(tool_result, str):
            tool_result = ""
        message = Message(role="tool", content=_format_tool_call_content(tool_name, tool_args, tool_result))
        entries.append(
            SearchableMessageEntry(
                message_id=message_id,
                message=message,
                tool_name=tool_name,
                tool_args=tool_args,
                tool_result=tool_result,
                tool_call_id=_tool_call_id_from_raw(raw, tool_name, tool_args),
            )
        )
    return entries


def _collect_message_entries_for_view(events: List[Any], view: str) -> List[tuple[str, "Message"]]:
    """Collect view-specific message IDs and messages from typed events or raw dict events."""
    return [(entry.message_id, entry.message) for entry in _collect_searchable_message_entries_for_view(events, view)]


def _collect_messages_for_view(events: List[Any], view: str) -> List["Message"]:
    """Collect view-specific messages from typed events or raw dict events."""
    return [message for _, message in _collect_message_entries_for_view(events, view)]


def _count_turns(messages: List["Message"], *, skip_system: bool, number_system: bool) -> int:
    turns = 0
    for msg in messages:
        if skip_system and msg.role == "system":
            continue
        if msg.role != "system" or number_system:
            turns += 1
    return turns


def _metadata_from_dict(data: Dict[str, Any]) -> "TranscriptMetadata":
    return TranscriptMetadata(
        kind=data["kind"],
        seed_id=data["seed_id"],
        concept=data["concept"],
        target=data["target"],
        auditor_model=data["auditor_model"],
        target_reasoning_effort=data.get("target_reasoning_effort"),
        auditor_reasoning_effort=data.get("auditor_reasoning_effort"),
        factors=data.get("factors"),
    )


def _edit_from_dict(edit_data: Dict[str, Any]) -> Edit | None:
    edit_type = edit_data["type"]
    if edit_type == EditType.ADD_MESSAGE.value:
        return AddMessageEdit(message=Message(**edit_data["message"]))
    if edit_type == EditType.SET_SYSTEM_MESSAGE.value:
        return SetSystemMessageEdit(message=Message(**edit_data["message"]))
    if edit_type == EditType.TOOL_CALL.value:
        return ToolCallEdit(
            tool_name=edit_data["tool_name"],
            tool_args=edit_data.get("tool_args", {}),
            tool_result=edit_data.get("tool_result", ""),
        )
    return None


def _event_from_dict(event_data: Dict[str, Any]) -> "TranscriptEvent | None":
    edit_data = event_data["edit"]
    edit = _edit_from_dict(edit_data)
    if edit is None:
        return None
    return TranscriptEvent(
        view=event_data["view"],
        actor=event_data["actor"],
        edit=edit,
        raw=event_data.get("raw") if isinstance(event_data.get("raw"), dict) else None,
    )


def _transcript_from_dict(data: Dict[str, Any]) -> "Transcript":
    events: List[TranscriptEvent] = []
    for event_data in data.get("events", []):
        event = _event_from_dict(event_data)
        if event is not None:
            events.append(event)
    llm_calls: List[LLMCallTrace] = []
    for call_data in data.get("llm_calls", []):
        if not isinstance(call_data, dict):
            continue
        try:
            llm_calls.append(LLMCallTrace(**call_data))
        except Exception:
            log.warning("Skipping malformed LLM call trace in transcript")
            continue
    return Transcript(
        metadata=_metadata_from_dict(data),
        events=events,
        llm_calls=llm_calls,
        stop_reason=data.get("stop_reason"),
    )


class TranscriptEvent(BaseModel):
    """Single event in the transcript."""
    view: Union[str, List[str]]  # Which views this event affects
    actor: Literal["auditor", "target", "tool", "system"]
    edit: Edit
    raw: Optional[Dict[str, Any]] = None  # Raw API request/response for debugging


class TranscriptMetadata(BaseModel):
    """Metadata for a transcript."""
    kind: str
    seed_id: str
    concept: str
    target: str
    auditor_model: str
    target_reasoning_effort: Optional[str] = None
    auditor_reasoning_effort: Optional[str] = None
    factors: Optional[Dict[str, str]] = None


class LLMCallTrace(BaseModel):
    """One owned LLM invocation captured during rollout."""
    call_id: str
    source: str
    api_mode: str
    request: Any = None
    response: Any = None
    derived: Dict[str, Any] = Field(default_factory=dict)
    message_ids: List[str] = Field(default_factory=list)


class Transcript(BaseModel):
    """
    Complete transcript for a multi-turn audit.
    
    Events are append-only. Views are materialized at read time via collect_messages().
    """
    metadata: TranscriptMetadata
    events: List[TranscriptEvent] = Field(default_factory=list)
    llm_calls: List[LLMCallTrace] = Field(default_factory=list)
    stop_reason: Optional[str] = None

    def add_event(self, event: TranscriptEvent) -> None:
        """Append an event to the transcript."""
        self.events.append(event)

    def append_llm_call(
        self,
        *,
        source: str,
        api_mode: str,
        request: Any,
        response: Any,
        derived: Dict[str, Any] | None = None,
    ) -> str:
        """Append one owned LLM call and return its stable transcript-local ID."""
        call_id = f"llm:{len(self.llm_calls)}"
        self.llm_calls.append(
            LLMCallTrace(
                call_id=call_id,
                source=source,
                api_mode=api_mode,
                request=request,
                response=response,
                derived=derived or {},
            )
        )
        return call_id

    def link_llm_call_to_message(self, call_id: str, message_id: str) -> None:
        """Attach a synthetic transcript message ID to an owned LLM call."""
        for llm_call in self.llm_calls:
            if llm_call.call_id != call_id:
                continue
            if message_id not in llm_call.message_ids:
                llm_call.message_ids.append(message_id)
            return

    def collect_messages(
        self,
        view: str,
    ) -> List[Message]:
        """
        Collect messages for a specific view.
        
        Args:
            view: Which view to collect ("auditor", "target", "combined")
        
        Returns:
            List of messages for the view.
        """
        # Use serialized edit types instead of class identity so helpers imported
        # through the current `p2m.*` package path remain interoperable.
        return _collect_messages_for_view(self.events, view)

    def collect_messages_with_ids(
        self,
        view: str,
    ) -> List[tuple[str, Message]]:
        """Collect view-specific synthetic message IDs alongside display messages."""
        return _collect_message_entries_for_view(self.events, view)

    def collect_searchable_messages_with_ids(
        self,
        view: str,
    ) -> List[SearchableMessageEntry]:
        """Collect view-specific searchable message entries with tool-call metadata when available."""
        return _collect_searchable_message_entries_for_view(self.events, view)

    def count_turns(
        self,
        view: str,
        *,
        skip_system: bool = True,
        number_system: bool = False,
    ) -> int:
        """Count numbered turns for a view using the same rules as formatting."""
        return _count_turns(
            self.collect_messages(view),
            skip_system=skip_system,
            number_system=number_system,
        )

    def format_transcript(
        self,
        view: str,
        skip_system: bool = True,
        numbered: bool = False,
        number_system: bool = False,
    ) -> str:
        """
        Format transcript as a string for display/judging.
        Uses compact format: Role:\nContent with blank line separation.
        If numbered=True, prefixes messages with [Turn N]. System messages can
        be included without consuming turn numbers by leaving number_system=False.
        """
        messages = self.collect_messages(view)
        lines = []
        turn = 0
        for msg in messages:
            if skip_system and msg.role == "system":
                continue
            role = ROLE_LABEL_BY_ROLE.get(msg.role, msg.role.capitalize())
            should_number = numbered and (msg.role != "system" or number_system)
            if should_number:
                turn += 1
            prefix = f"[Turn {turn}] " if should_number else ""
            lines.append(f"{prefix}{role}:\n{msg.content}")
        return "\n\n".join(lines)

    def format_transcript_xml(
        self,
        view: str,
        skip_system: bool = True,
        max_message_chars: int | None = None,
    ) -> tuple[str, Dict[str, str]]:
        """
        Format a transcript view as indexed XML for citation-friendly judging.

        Returns the XML transcript plus a mapping from XML message index to
        synthetic message ID.
        """
        limit = max_message_chars if max_message_chars is not None else _DEFAULT_MAX_MESSAGE_CHARS
        entries = self.collect_messages_with_ids(view)
        parts: List[str] = []
        index_to_message_id: Dict[str, str] = {}
        xml_index = 1

        for message_id, msg in entries:
            if skip_system and msg.role == "system":
                continue
            index_to_message_id[str(xml_index)] = message_id
            tag = ROLE_LABEL_BY_ROLE.get(msg.role, msg.role).lower()
            raw_content = msg.content or ""
            truncated = len(raw_content) > limit
            if truncated:
                raw_content = raw_content[:limit] + f"\n[... truncated, {len(msg.content)} chars total ...]"
            content = _escape_xml_text(raw_content)
            truncated_attr = ' truncated="true"' if truncated else ""
            parts.append(f'<{tag} index="{xml_index}"{truncated_attr}>\n{content}\n</{tag}>')
            xml_index += 1

        transcript = "<transcript>\n"
        if parts:
            transcript += "\n\n".join(parts) + "\n"
        transcript += "</transcript>"
        return transcript, index_to_message_id

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "kind": self.metadata.kind,
            "seed_id": self.metadata.seed_id,
            "concept": self.metadata.concept,
            "events": [e.model_dump() for e in self.events],
            "llm_calls": [call.model_dump() for call in self.llm_calls],
            "stop_reason": self.stop_reason,
            "target": self.metadata.target,
            "auditor_model": self.metadata.auditor_model,
            "target_reasoning_effort": self.metadata.target_reasoning_effort,
            "auditor_reasoning_effort": self.metadata.auditor_reasoning_effort,
        }
        if self.metadata.factors:
            result["factors"] = self.metadata.factors
        return result

    def save_jsonl(self, path: Path) -> None:
        """Append transcript as single JSONL line."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(self.to_dict(), ensure_ascii=False) + "\n")

    @classmethod
    def load_jsonl(cls, path: Path) -> List["Transcript"]:
        """Load all transcripts from JSONL file."""
        transcripts = []
        if not path.exists():
            return transcripts
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    transcripts.append(_transcript_from_dict(json.loads(line)))
                except Exception:
                    log.warning("Skipping malformed JSONL line in %s", path)
        return transcripts


def count_transcript_turns(
    transcript: Any,
    view: str,
    *,
    skip_system: bool = True,
    number_system: bool = False,
) -> int:
    """Count numbered turns from either a Transcript instance or serialized dict."""
    if isinstance(transcript, Transcript):
        return transcript.count_turns(
            view,
            skip_system=skip_system,
            number_system=number_system,
        )

    if isinstance(transcript, dict):
        events = transcript.get("events", [])
    else:
        events = getattr(transcript, "events", [])

    return _count_turns(
        _collect_messages_for_view(list(events), view),
        skip_system=skip_system,
        number_system=number_system,
    )
