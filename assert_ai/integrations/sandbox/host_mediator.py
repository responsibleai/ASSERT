# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Trusted host-side mediation service and append-only action ledger."""
from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from assert_ai.core.sanitization import sanitize_untrusted_value

from .mediator import ActionMediator
from .mocks import MockLibrary
from .policy import MediationPolicy
from .records import MediationDecision

_MAX_REQUEST_BYTES = 1024 * 1024


def decision_payload(decision: MediationDecision) -> dict[str, Any]:
    """Serialize the full decision needed by the target-side client."""
    return {
        **decision.evidence(),
        "is_error": decision.is_error,
    }


class HostMediationLedger:
    """Own mediation decisions outside the evaluated target.

    Decision transitions are appended to ``ledger_path`` before a response is
    returned to the target. Completed evidence rows are kept in trusted host
    memory for ASSERT to drain into the normal inference transcript.
    """

    def __init__(
        self,
        mediator: ActionMediator,
        *,
        ledger_path: Path,
        expected_case_id: str | None = None,
    ) -> None:
        self._mediator = mediator
        self.ledger_path = ledger_path.resolve()
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.parent.chmod(0o700)
        self.expected_case_id = expected_case_id
        self._pending: dict[str, dict[str, Any]] = {}
        self._ready: list[dict[str, Any]] = []
        self._seen: set[str] = set()
        self._next_sequence = 0
        self._registered = False
        self._lock = threading.Lock()

    @property
    def registered(self) -> bool:
        with self._lock:
            return self._registered

    def register(self, claimed_case_id: str | None) -> None:
        if self.expected_case_id is not None and claimed_case_id != self.expected_case_id:
            raise ValueError(
                "mediator registration case_id does not match the ASSERT-owned sandbox case"
            )
        with self._lock:
            self._registered = True

    def mediate(self, pre_context: dict[str, Any]) -> MediationDecision:
        tool_call = pre_context.get("tool_call") or {}
        call_id = str(tool_call.get("id") or "").strip()
        tool = str(tool_call.get("name") or "").strip()
        args = tool_call.get("args") or {}
        if not call_id or not tool or not isinstance(args, dict):
            raise ValueError("host mediator requires tool_call.id, tool_call.name, and object args")

        session = pre_context.get("session") or {}
        claimed_case_id = str(session.get("case_id") or "") or None
        if self.expected_case_id is not None and claimed_case_id != self.expected_case_id:
            raise ValueError(
                "mediated call case_id does not match the ASSERT-owned sandbox case"
            )
        with self._lock:
            if call_id in self._seen:
                raise ValueError(f"duplicate mediation call id {call_id!r}")
            decision = self._mediator.plan(pre_context)
            sequence = self._next_sequence
            self._next_sequence += 1
            row = sanitize_untrusted_value({
                "id": call_id,
                "sequence": sequence,
                "tool": tool,
                "args": dict(args),
                "case_id": self.expected_case_id or claimed_case_id,
                "mode": decision.mode,
                "matched": decision.matched,
                "reason": decision.reason,
                "decision_reason": decision.reason,
                "policy_note": decision.policy_note or None,
                "flagged": decision.flagged,
                "returned": decision.returned,
                "is_error": decision.is_error,
                "real_executed": False if decision.mode != "pass" else None,
                "execution_status": (
                    "not_executed" if decision.mode != "pass" else "pending"
                ),
                "mock_source": decision.mock_source,
                "replay": decision.replay,
                "evidence_source": "host_mediator",
                "decision_source": "host_mediator",
                "result_source": "host_mediator" if decision.mode != "pass" else None,
                "completion_status": "complete" if decision.mode != "pass" else "pending",
            })
            self._seen.add(call_id)
            self._append_transition({"phase": "decision", **row})
            if decision.mode == "pass":
                self._pending[call_id] = row
            else:
                self._ready.append(row)
        return decision

    def complete(
        self,
        *,
        call_id: str,
        returned: Any,
        is_error: bool,
        real_executed: bool,
    ) -> None:
        with self._lock:
            row = self._pending.pop(call_id, None)
            if row is None:
                raise ValueError(f"unknown or already-completed mediation call {call_id!r}")
            completed = sanitize_untrusted_value({
                **row,
                "returned": returned,
                "is_error": bool(is_error),
                "real_executed": bool(real_executed),
                "execution_status": "executed" if real_executed else "not_executed",
                "result_source": "target_reported",
                "completion_status": "complete",
            })
            self._append_transition({"phase": "completion", **completed})
            self._ready.append(completed)

    def drain(self) -> list[dict[str, Any]]:
        """Return completed rows plus attempts whose target never reported completion."""
        with self._lock:
            incomplete = [
                {
                    **row,
                    "returned": None,
                    "is_error": True,
                    "real_executed": None,
                    "execution_status": "unknown",
                    "result_source": "not_reported",
                    "completion_status": "missing",
                }
                for row in self._pending.values()
            ]
            for row in incomplete:
                self._append_transition({"phase": "completion_missing", **row})
            rows = sorted(
                [*self._ready, *incomplete],
                key=lambda row: int(row["sequence"]),
            )
            self._ready.clear()
            self._pending.clear()
            return rows

    def _append_transition(self, row: dict[str, Any]) -> None:
        with self.ledger_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        self.ledger_path.chmod(0o600)


class _HostMediatorHandler(BaseHTTPRequestHandler):
    ledger: HostMediationLedger
    access_token: str

    def log_message(self, format: str, *args: Any) -> None:
        return None

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:
        supplied = self.headers.get("authorization", "")
        if not secrets.compare_digest(supplied, f"Bearer {self.access_token}"):
            self._send(401, {"error": "invalid_mediator_token"})
            return
        try:
            length = int(self.headers.get("content-length", "0") or 0)
            if length <= 0 or length > _MAX_REQUEST_BYTES:
                raise ValueError("invalid mediation request size")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("mediation request must be a JSON object")
            if self.path == "/mediate":
                pre_context = payload.get("pre_context")
                if not isinstance(pre_context, dict):
                    raise ValueError("mediate requires object pre_context")
                decision = self.ledger.mediate(pre_context)
                self._send(200, {"decision": decision_payload(decision)})
                return
            if self.path == "/register":
                claimed_case_id = str(payload.get("case_id") or "") or None
                self.ledger.register(claimed_case_id)
                self._send(200, {"status": "registered"})
                return
            if self.path == "/complete":
                call_id = str(payload.get("call_id") or "")
                self.ledger.complete(
                    call_id=call_id,
                    returned=payload.get("returned"),
                    is_error=bool(payload.get("is_error", False)),
                    real_executed=bool(payload.get("real_executed", False)),
                )
                self._send(200, {"status": "recorded"})
                return
            self._send(404, {"error": "not_found"})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._send(400, {"error": "invalid_request", "detail": str(exc)})

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.send_header("connection", "close")
        self.end_headers()
        self.wfile.write(body)


def start_host_mediator(
    *,
    policy_path: Path,
    mocks_path: Path | None,
    cassette_dir: Path | None,
    ledger_path: Path,
    access_token: str,
    case_id: str | None = None,
) -> tuple[ThreadingHTTPServer, threading.Thread, int, HostMediationLedger]:
    policy = MediationPolicy.from_yaml(policy_path)
    mocks = MockLibrary.from_yaml(mocks_path) if mocks_path is not None else MockLibrary.empty()
    if cassette_dir is not None:
        mocks = MockLibrary(mocks.rules, cassette_dir=cassette_dir)
    mediator = ActionMediator(policy, mocks=mocks.fresh(), cassette_dir=cassette_dir)
    ledger = HostMediationLedger(
        mediator,
        ledger_path=ledger_path,
        expected_case_id=case_id,
    )
    handler = type(
        "SandboxHostMediatorHandler",
        (_HostMediatorHandler,),
        {"ledger": ledger, "access_token": access_token},
    )
    server = ThreadingHTTPServer(("0.0.0.0", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, int(server.server_port), ledger
