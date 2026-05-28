# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""External connector for OpenClaw.

Config key: connector: examples.agents.openclaw

ASSERT delegates the conversation to the external agent and records what
it says.  Each Adapter instance spins up its own Docker container via
docker-compose so concurrent inference workers never collide.
"""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

COMPOSE_FILE = Path(__file__).resolve().parent / "docker-compose.yml"
MESSAGE_TIMEOUT_S = 300
CONTAINER_SERVICE = "openclaw-gateway"
SESSION_DIR = "/root/.openclaw/agents/main/sessions"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class Adapter:
    def __init__(self, scenario: dict) -> None:
        compose = scenario.get("compose_file")
        self._compose_file = Path(compose) if compose else COMPOSE_FILE
        self._service = str(scenario.get("service") or CONTAINER_SERVICE)
        self._timeout = float(scenario.get("message_timeout_s") or MESSAGE_TIMEOUT_S)
        self._session_id = ""
        self._project = ""
        self._container = ""
        self._event_cursor = 0

    # -- lifecycle -----------------------------------------------------------

    def open(self) -> None:
        self._session_id = uuid.uuid4().hex[:16]
        self._project = f"openclaw-{self._session_id}"
        self._compose(["up", "-d", "--wait", self._service])
        self._container = self._compose(["ps", "-q", self._service]).stdout.strip()
        self._event_cursor = 0

    def close(self) -> None:
        try:
            self._compose(["down", "--timeout", "10", "--volumes"])
        except Exception:
            pass
        self._container = ""

    # -- messaging -----------------------------------------------------------

    def send_message(self, text: str, *, history: list[dict] | None = None) -> dict[str, Any]:
        del history
        raw_output = self._exec(
            "openclaw", "agent",
            "--session-id", self._session_id,
            "--message", text,
            "--json",
        )
        raw_output = ANSI_RE.sub("", raw_output)

        payload = _try_parse_json(raw_output)
        if payload is None:
            return {"text": raw_output, "raw": raw_output}

        events = self._read_new_events()
        payload["session_events"] = events

        content = (
            _text_from_events(events)
            or _text_from_payload(payload)
            or payload.get("text")
            or payload.get("content")
            or payload.get("reply")
            or raw_output
        )
        return {"text": str(content), "raw": payload}

    # -- internals -----------------------------------------------------------

    def _compose(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        cmd = [
            "docker", "compose",
            "-f", str(self._compose_file),
            "-p", self._project,
            *args,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(
                f"docker compose {' '.join(args)} failed: "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        return proc

    def _exec(self, *args: str) -> str:
        if not self._container:
            raise RuntimeError("Adapter must be opened before sending messages")
        result = subprocess.run(
            ["docker", "exec", self._container, *args],
            capture_output=True, text=True, timeout=self._timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"OpenClaw agent exit {result.returncode}: "
                f"{result.stderr.strip() or '(no stderr)'}"
            )
        return result.stderr.strip() or result.stdout.strip()

    def _read_new_events(self) -> list[dict[str, Any]]:
        raw = self._exec("cat", f"{SESSION_DIR}/{self._session_id}.jsonl")
        events = [json.loads(ln) for ln in raw.splitlines() if ln.strip()]
        new = events[self._event_cursor:]
        self._event_cursor = len(events)
        return new


# -- response extraction helpers ---------------------------------------------


def _try_parse_json(text: str) -> dict[str, Any] | None:
    idx = text.find("{")
    if idx == -1:
        return None
    try:
        return json.loads(text[idx:])
    except json.JSONDecodeError:
        return None


def _text_from_payload(payload: dict[str, Any]) -> str:
    items = payload.get("payloads")
    if not isinstance(items, list):
        return ""
    parts = [p["text"].strip() for p in items
             if isinstance(p, dict) and isinstance(p.get("text"), str) and p["text"].strip()]
    return "\n\n".join(parts)


def _text_from_events(events: list[dict[str, Any]]) -> str:
    texts: list[str] = []
    for event in events:
        if event.get("type") != "message":
            continue
        msg = event.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        for part in msg.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "text":
                t = part.get("text", "")
                if isinstance(t, str) and t.strip():
                    texts.append(t.strip())
    return "\n\n".join(texts)
