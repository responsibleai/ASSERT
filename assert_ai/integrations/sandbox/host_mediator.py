# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Trusted host-side mediation service and append-only action ledger."""
from __future__ import annotations

import json
import secrets
import socket
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from assert_ai.core.action_claims import ActionClaim, make_action_claim
from assert_ai.core.sanitization import sanitize_untrusted_value

from .mediator import ActionMediator
from .mocks import MockLibrary
from .policy import MediationPolicy
from .records import MediationDecision

_MAX_REQUEST_BYTES = 1024 * 1024
_DEFAULT_MAX_WORKERS = 16
_DEFAULT_CONNECTION_TIMEOUT_S = 5.0
_DEFAULT_MAX_ACTIONS = 64


def _reject_nonfinite_json(value: str) -> Any:
    raise ValueError(f"non-finite JSON value {value!r} is not supported")


def _public_ledger_row(row: dict[str, Any]) -> dict[str, Any]:
    """Sanitize persisted evidence and assign a host-owned public call ID."""
    sanitized = sanitize_untrusted_value(row)
    if not isinstance(sanitized, dict):
        raise TypeError("host action row must be an object")
    sequence = row.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise ValueError("host action row requires a non-negative integer sequence")
    sanitized["id"] = f"host-action-{sequence}"
    return sanitized


@dataclass(frozen=True)
class HostActionBatch:
    """Sanitized evidence plus private digests used only for reconciliation."""

    rows: list[dict[str, Any]]
    claims: list[ActionClaim]


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
        max_actions: int = _DEFAULT_MAX_ACTIONS,
    ) -> None:
        if max_actions < 1:
            raise ValueError("max_actions must be at least 1")
        self._mediator = mediator
        self.ledger_path = ledger_path.resolve()
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.parent.chmod(0o700)
        self.expected_case_id = expected_case_id
        self.max_actions = max_actions
        self._pending: dict[str, dict[str, Any]] = {}
        self._completed: dict[str, dict[str, Any]] = {}
        self._ready: list[dict[str, Any]] = []
        self._seen: set[str] = set()
        self._claims: dict[str, ActionClaim] = {}
        self._next_sequence = 0
        self._limit_row: dict[str, Any] | None = None
        self._limit_summary_persisted = False
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
        tool_call = pre_context.get("tool_call")
        if not isinstance(tool_call, dict):
            raise TypeError("host mediator requires an object tool_call")
        call_id = str(tool_call.get("id") or "").strip()
        tool = str(tool_call.get("name") or "").strip()
        raw_args = tool_call.get("args") if "args" in tool_call else {}
        if not isinstance(raw_args, dict):
            raise TypeError("host mediator requires object tool_call.args")
        args = dict(raw_args)
        if not call_id or not tool:
            raise ValueError("host mediator requires tool_call.id and tool_call.name")
        claim = make_action_claim(
            kind="call",
            call_id=call_id,
            tool=tool,
            arguments=args,
            arguments_supplied=True,
        )

        session = pre_context.get("session") or {}
        claimed_case_id = str(session.get("case_id") or "") or None
        if self.expected_case_id is not None and claimed_case_id != self.expected_case_id:
            raise ValueError(
                "mediated call case_id does not match the ASSERT-owned sandbox case"
            )
        with self._lock:
            if call_id in self._seen:
                raise ValueError(f"duplicate mediation call id {call_id!r}")
            if self._next_sequence >= self.max_actions:
                reason = f"host mediator action limit of {self.max_actions} was reached"
                if self._limit_row is None:
                    sequence = self._next_sequence
                    self._next_sequence += 1
                    attempt = {
                        "id": call_id,
                        "sequence": sequence,
                        "tool": tool,
                        "args": args,
                        "case_id": self.expected_case_id or claimed_case_id,
                        "mode": "pending",
                        "matched": "",
                        "reason": "",
                        "decision_reason": "",
                        "policy_note": None,
                        "flagged": False,
                        "returned": None,
                        "is_error": False,
                        "real_executed": False,
                        "execution_status": "not_executed",
                        "mock_source": None,
                        "replay": None,
                        "evidence_source": "host_mediator",
                        "decision_source": "host_mediator",
                        "result_source": None,
                        "completion_status": "pending",
                        "decision_status": "pending",
                        "suppressed_attempt_count": 1,
                    }
                    self._append_transition({"phase": "attempt", **attempt})
                    rejected = {
                        **attempt,
                        "mode": "block",
                        "reason": reason,
                        "decision_reason": reason,
                        "returned": {"status": "blocked", "reason": reason},
                        "is_error": True,
                        "result_source": "host_mediator",
                        "completion_status": "complete",
                        "decision_status": "error",
                    }
                    self._append_transition({"phase": "decision_error", **rejected})
                    self._seen.add(call_id)
                    self._claims[call_id] = claim
                    self._ready.append(rejected)
                    self._limit_row = rejected
                else:
                    self._limit_row["suppressed_attempt_count"] = (
                        int(self._limit_row["suppressed_attempt_count"]) + 1
                    )
                raise ValueError(reason)
            sequence = self._next_sequence
            self._next_sequence += 1
            attempt = {
                "id": call_id,
                "sequence": sequence,
                "tool": tool,
                "args": args,
                "case_id": self.expected_case_id or claimed_case_id,
                "mode": "pending",
                "matched": "",
                "reason": "",
                "decision_reason": "",
                "policy_note": None,
                "flagged": False,
                "returned": None,
                "is_error": False,
                "real_executed": False,
                "execution_status": "not_executed",
                "mock_source": None,
                "replay": None,
                "evidence_source": "host_mediator",
                "decision_source": "host_mediator",
                "result_source": None,
                "completion_status": "pending",
                "decision_status": "pending",
            }
            self._append_transition({"phase": "attempt", **attempt})
            self._seen.add(call_id)
            self._claims[call_id] = claim
            try:
                decision = self._mediator.plan(pre_context)
            except Exception as exc:
                reason = f"policy evaluation failed: {type(exc).__name__}: {exc}"
                failed = {
                    **attempt,
                    "mode": "block",
                    "flagged": True,
                    "returned": {
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                    "is_error": True,
                    "reason": reason,
                    "decision_reason": reason,
                    "result_source": "host_mediator",
                    "completion_status": "complete",
                    "decision_status": "error",
                }
                self._append_transition({"phase": "decision_error", **failed})
                self._ready.append(failed)
                raise ValueError(reason) from exc
            row = {
                **attempt,
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
                "result_source": "host_mediator" if decision.mode != "pass" else None,
                "completion_status": "complete" if decision.mode != "pass" else "pending",
                "decision_status": "complete",
            }
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
            completed_before = self._completed.get(call_id)
            if completed_before is not None:
                if (
                    completed_before.get("returned") == returned
                    and completed_before.get("is_error") == bool(is_error)
                    and completed_before.get("real_executed") == bool(real_executed)
                ):
                    return
                raise ValueError(f"conflicting completion for mediation call {call_id!r}")
            row = self._pending.get(call_id)
            if row is None:
                raise ValueError(f"unknown or already-completed mediation call {call_id!r}")
            completed = {
                **row,
                "returned": returned,
                "is_error": bool(is_error),
                "real_executed": bool(real_executed),
                "execution_status": "executed" if real_executed else "not_executed",
                "result_source": "target_reported",
                "completion_status": "complete",
            }
            self._append_transition({"phase": "completion", **completed})
            self._pending.pop(call_id, None)
            self._completed[call_id] = completed
            self._ready.append(completed)

    def _drain_batch(self, *, finalize_pending: bool) -> HostActionBatch:
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
        ] if finalize_pending else []
        for row in incomplete:
            self._append_transition({"phase": "completion_missing", **row})
        if finalize_pending or not self._pending:
            ready = list(self._ready)
            held_ready: list[dict[str, Any]] = []
        else:
            earliest_pending = min(
                int(row["sequence"])
                for row in self._pending.values()
            )
            ready = [
                row for row in self._ready
                if int(row["sequence"]) < earliest_pending
            ]
            held_ready = [
                row for row in self._ready
                if int(row["sequence"]) >= earliest_pending
            ]
        if not finalize_pending and self._limit_row is not None:
            # Keep the bounded overflow marker mutable until shutdown. Emitting
            # it earlier would make subsequent rejected attempts invisible in
            # the normal inference evidence.
            ready = [row for row in ready if row is not self._limit_row]
            if all(row is not self._limit_row for row in held_ready):
                held_ready.append(self._limit_row)
        if (
            finalize_pending
            and self._limit_row is not None
            and not self._limit_summary_persisted
        ):
            self._append_transition({"phase": "overflow_summary", **self._limit_row})
            self._limit_summary_persisted = True
        rows = sorted(
            [*ready, *incomplete],
            key=lambda row: int(row["sequence"]),
        )
        claims = [self._claims[str(row["id"])] for row in rows]
        public_rows = [_public_ledger_row(row) for row in rows]
        self._ready = held_ready
        if finalize_pending:
            self._pending.clear()
        for row in rows:
            self._claims.pop(str(row["id"]), None)
        return HostActionBatch(rows=public_rows, claims=claims)

    def drain_ready_batch(self) -> HostActionBatch:
        """Drain completed rows while leaving passed calls completable."""
        with self._lock:
            return self._drain_batch(finalize_pending=False)

    def drain_batch(self) -> HostActionBatch:
        """Finalize and drain all sanitized rows and private claims."""
        with self._lock:
            return self._drain_batch(finalize_pending=True)

    def drain(self) -> list[dict[str, Any]]:
        """Compatibility wrapper returning only sanitized evidence rows."""
        return self.drain_batch().rows

    def _append_transition(self, row: dict[str, Any]) -> None:
        public_row = _public_ledger_row(row)
        with self.ledger_path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    public_row,
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                ) + "\n"
            )
        self.ledger_path.chmod(0o600)


class _BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Limit target-controlled host threads and stalled connection lifetime."""

    daemon_threads = True

    def __init__(
        self,
        server_address,
        request_handler_class,
        *,
        max_workers: int,
        connection_timeout_s: float,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        if connection_timeout_s <= 0:
            raise ValueError("connection_timeout_s must be positive")
        self.max_workers = max_workers
        self.connection_timeout_s = connection_timeout_s
        self._worker_slots = threading.BoundedSemaphore(max_workers)
        self._deadline_lock = threading.Lock()
        self._deadline_timers: dict[int, threading.Timer] = {}
        super().__init__(server_address, request_handler_class)

    def wait_for_idle(self) -> bool:
        """Wait until every accepted mediator request has left its worker."""
        acquired = 0
        deadline = time.monotonic() + self.connection_timeout_s + 1.0
        try:
            while acquired < self.max_workers:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._worker_slots.acquire(timeout=remaining):
                    return False
                acquired += 1
            return True
        finally:
            for _ in range(acquired):
                self._worker_slots.release()

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._worker_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        timer = threading.Timer(
            self.connection_timeout_s,
            self._expire_request,
            args=(request,),
        )
        timer.daemon = True
        with self._deadline_lock:
            self._deadline_timers[id(request)] = timer
        timer.start()
        try:
            super().process_request(request, client_address)
        except Exception:
            with self._deadline_lock:
                self._deadline_timers.pop(id(request), None)
            timer.cancel()
            self._worker_slots.release()
            raise

    @staticmethod
    def _expire_request(request: Any) -> None:
        try:
            request.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            request.close()
        except OSError:
            pass

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            request.settimeout(self.connection_timeout_s)
            super().process_request_thread(request, client_address)
        finally:
            with self._deadline_lock:
                timer = self._deadline_timers.pop(id(request), None)
            if timer is not None:
                timer.cancel()
            self._worker_slots.release()


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
            payload = json.loads(
                self.rfile.read(length),
                parse_constant=_reject_nonfinite_json,
            )
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
        body = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
        try:
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.send_header("connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            self.close_connection = True


def start_host_mediator(
    *,
    policy_path: Path,
    mocks_path: Path | None,
    cassette_dir: Path | None,
    ledger_path: Path,
    access_token: str,
    case_id: str | None = None,
    max_workers: int = _DEFAULT_MAX_WORKERS,
    connection_timeout_s: float = _DEFAULT_CONNECTION_TIMEOUT_S,
    max_actions: int = _DEFAULT_MAX_ACTIONS,
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
        max_actions=max_actions,
    )
    handler = type(
        "SandboxHostMediatorHandler",
        (_HostMediatorHandler,),
        {"ledger": ledger, "access_token": access_token},
    )
    server = _BoundedThreadingHTTPServer(
        ("0.0.0.0", 0),
        handler,
        max_workers=max_workers,
        connection_timeout_s=connection_timeout_s,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, int(server.server_port), ledger
