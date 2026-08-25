# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Persistent diagnostics for model responses that fail downstream parsing."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from assert_ai.core.io import write_json
from assert_ai.core.model_client import (
    ModelResponse,
    build_llm_call_trace,
    to_jsonable,
)

log = logging.getLogger(__name__)

_SAFE_STAGE = re.compile(r"[^A-Za-z0-9_.-]+")


def write_llm_failure_diagnostic(
    response: ModelResponse,
    *,
    diagnostics_dir: str | Path,
    stage: str,
    reason: str,
    attempt: int | None = None,
) -> Path | None:
    """Persist the complete failed model exchange without masking its error.

    Request payload credentials are redacted by ``build_llm_call_trace``. The
    response remains complete so a user can distinguish malformed structured
    output from truncation or a provider-specific response shape.
    """

    safe_stage = _SAFE_STAGE.sub("-", stage).strip("-.") or "llm"
    created_at = datetime.now(UTC)
    filename = f"{created_at.strftime('%Y%m%dT%H%M%S.%fZ')}-{uuid.uuid4().hex[:8]}.json"
    path = Path(diagnostics_dir).expanduser() / safe_stage / filename
    payload = {
        "schema_version": 1,
        "created_at": created_at.isoformat(),
        "stage": stage,
        "reason": reason,
        "response_metadata": {
            "model": response.model,
            "response_id": response.response_id,
            "finish_reason": response.finish_reason,
            "status": response.status,
            "incomplete_details": to_jsonable(response.incomplete_details),
            "api_mode": response.api_mode,
        },
        "llm_call": build_llm_call_trace(response, source=stage),
    }
    if attempt is not None:
        payload["attempt"] = attempt

    try:
        write_json(path, payload)
    except Exception as exc:  # noqa: BLE001 - diagnostics must never replace the original failure
        log.warning("[%s] Failed to write model-response diagnostic at %s: %s", stage, path, exc)
        return None
    return path


def llm_failure_error(
    response: ModelResponse,
    *,
    diagnostics_dir: str | Path,
    stage: str,
    reason: str,
    message: str,
    attempt: int | None = None,
) -> ValueError:
    """Build the original error plus a full-response diagnostic when possible."""

    diagnostic_path = write_llm_failure_diagnostic(
        response,
        diagnostics_dir=diagnostics_dir,
        stage=stage,
        reason=reason,
        attempt=attempt,
    )
    if diagnostic_path is not None:
        message = f"{message} Full response diagnostic: {diagnostic_path}"
    return ValueError(message)
