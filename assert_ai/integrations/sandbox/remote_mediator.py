# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Target-side client for ASSERT's trusted host mediation service."""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import replace
from typing import Any

from .records import MediationDecision

_MAX_COMPLETION_RESULT_BYTES = 512 * 1024
_MAX_COMPLETION_PREVIEW_BYTES = 64 * 1024
_MAX_COMPLETION_ERROR_TYPE_BYTES = 1024
_MAX_COMPLETION_ERROR_MESSAGE_BYTES = 16 * 1024


def _bounded_text(value: Any, *, max_bytes: int, fallback: str) -> str:
    """Render target-controlled diagnostics within a strict UTF-8 byte bound."""
    try:
        text = str(value)
    except Exception:
        text = fallback
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _bounded_completion_value(value: Any) -> Any:
    """Keep an honest completion report below the mediator request limit."""
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            default=str,
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError, RecursionError) as exc:
        return {
            "_assert_result_unserializable": True,
            "error_type": _bounded_text(
                type(exc).__name__,
                max_bytes=_MAX_COMPLETION_ERROR_TYPE_BYTES,
                fallback="SerializationError",
            ),
            "message": _bounded_text(
                exc,
                max_bytes=_MAX_COMPLETION_ERROR_MESSAGE_BYTES,
                fallback="completion result serialization failed",
            ),
        }
    if len(encoded) <= _MAX_COMPLETION_RESULT_BYTES:
        return value
    return {
        "_assert_result_truncated": True,
        "original_size_bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "preview": encoded[:_MAX_COMPLETION_PREVIEW_BYTES].decode(errors="replace"),
    }


class _HostMediatorTransportError(RuntimeError):
    """The relay or host mediator was not reachable yet."""


def _decision_from_payload(payload: dict[str, Any]) -> MediationDecision:
    mode = str(payload.get("mode") or "block")
    if mode not in {"pass", "mock", "block"}:
        raise RuntimeError(f"host mediator returned unknown mode {mode!r}")
    return MediationDecision(
        mode=mode,  # type: ignore[arg-type]
        returned=payload.get("returned"),
        real_executed=bool(payload.get("real_executed", False)),
        reason=str(payload.get("reason") or payload.get("decision_reason") or ""),
        matched=str(payload.get("matched") or ""),
        is_error=bool(payload.get("is_error", False)),
        mock_source=str(payload["mock_source"]) if payload.get("mock_source") else None,
        replay=dict(payload["replay"]) if isinstance(payload.get("replay"), dict) else None,
        policy_note=str(payload.get("policy_note") or ""),
    )


class RemoteActionMediator:
    """Obtain pass/mock/block decisions from the trusted host before execution."""

    host_authoritative = True

    def __init__(
        self,
        base_url: str,
        access_token: str,
        *,
        case_id: str | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.timeout_s = timeout_s
        self._register(case_id)

    def _register(self, case_id: str | None) -> None:
        """Wait for the newly started relay without retrying policy/auth errors."""
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                self._post("/register", {"case_id": case_id})
                return
            except _HostMediatorTransportError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(0.1, remaining))

    def mediate(self, pre_context: dict[str, Any], execute_effective) -> MediationDecision:
        response = self._post("/mediate", {"pre_context": pre_context})
        decision_raw = response.get("decision")
        if not isinstance(decision_raw, dict):
            raise RuntimeError("host mediator returned no decision object")
        decision = _decision_from_payload(decision_raw)
        if decision.mode != "pass":
            return decision

        call_id = str((pre_context.get("tool_call") or {}).get("id") or "")
        args = dict((pre_context.get("tool_call") or {}).get("args") or {})
        if not hasattr(execute_effective, "real_executed"):
            message = (
                "host-authoritative pass execution requires an executor that tracks "
                "real_executed"
            )
            self._post("/complete", {
                "call_id": call_id,
                "returned": {
                    "status": "error",
                    "error_type": "UntrackedExecutor",
                    "message": message,
                },
                "is_error": True,
                "real_executed": False,
            })
            raise RuntimeError(message)

        # Fail closed on execution claims: only report a real side effect when
        # the executor explicitly tracked one.
        def _executed() -> bool:
            return bool(execute_effective.real_executed)

        try:
            returned = execute_effective(args)
        except Exception as exc:
            real_executed = _executed()
            self._post("/complete", {
                "call_id": call_id,
                "returned": _bounded_completion_value({
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }),
                "is_error": True,
                "real_executed": real_executed,
            })
            raise

        real_executed = _executed()
        is_error = not real_executed
        self._post("/complete", {
            "call_id": call_id,
            "returned": _bounded_completion_value(returned),
            "is_error": is_error,
            "real_executed": real_executed,
        })
        return replace(
            decision,
            returned=returned,
            real_executed=real_executed,
            is_error=is_error,
        )

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            default=str,
            allow_nan=False,
        ).encode()
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method="POST",
            headers={
                "authorization": f"Bearer {self.access_token}",
                "content-type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                decoded = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"host mediator rejected {path}: HTTP {exc.code}: {detail}") from exc
        except OSError as exc:
            raise _HostMediatorTransportError(
                f"host mediator request failed for {path}: {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"host mediator returned invalid JSON for {path}: {exc}"
            ) from exc
        if not isinstance(decoded, dict):
            raise RuntimeError("host mediator returned a non-object response")
        return decoded
