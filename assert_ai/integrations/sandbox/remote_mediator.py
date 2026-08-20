# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Target-side client for ASSERT's trusted host mediation service."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import replace
from typing import Any

from .records import MediationDecision


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
        self._post("/register", {"case_id": case_id})

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
        # Fail closed on execution claims: only report a real side effect when
        # the executor explicitly tracked one. An executor without the flag has
        # not proven it ran the tool, so do not assert that it did.
        def _executed() -> bool:
            return bool(getattr(execute_effective, "real_executed", False))

        try:
            returned = execute_effective(args)
        except Exception as exc:
            real_executed = _executed()
            self._post("/complete", {
                "call_id": call_id,
                "returned": {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
                "is_error": True,
                "real_executed": real_executed,
            })
            raise

        real_executed = _executed()
        is_error = not real_executed
        self._post("/complete", {
            "call_id": call_id,
            "returned": returned,
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
        body = json.dumps(payload, ensure_ascii=False, default=str).encode()
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
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"host mediator request failed for {path}: {exc}") from exc
        if not isinstance(decoded, dict):
            raise RuntimeError("host mediator returned a non-object response")
        return decoded
