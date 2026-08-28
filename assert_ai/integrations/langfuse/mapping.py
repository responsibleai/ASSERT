# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Pure mappings from ASSERT run artifacts to Langfuse public API payloads."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from assert_ai.core.judge import infer_judge_status
from assert_ai.integrations.langfuse.errors import LangfuseContractError


def trace_ids(*, run_id: str, inference_row: dict[str, Any]) -> tuple[str, str]:
    """Return deterministic OTLP trace and span IDs for an inference row."""
    test_case_id = _required_string(inference_row, "test_case_id", artifact="inference")
    kind = _required_string(inference_row, "type", artifact="inference")
    digest = hashlib.sha256(f"ASSERT:{run_id}:{kind}:{test_case_id}".encode()).hexdigest()
    return digest[:32], digest[32:48]


def inference_to_otlp_trace(
    inference_row: dict[str, Any],
    *,
    run_id: str,
    timestamp_ns: int,
) -> dict[str, Any]:
    """Map one current ``inference_set.jsonl`` row to one OTLP root span."""
    if timestamp_ns <= 0:
        raise LangfuseContractError("timestamp_ns must be a positive integer")
    test_case_id = _required_string(inference_row, "test_case_id", artifact="inference")
    kind = _required_string(inference_row, "type", artifact="inference")
    trace_id, span_id = trace_ids(run_id=run_id, inference_row=inference_row)
    behavior = str(inference_row.get("behavior") or "")

    trace_input = {
        "behavior": behavior,
        "dimensions": inference_row.get("dimensions") or {},
        "test_case_id": test_case_id,
        "type": kind,
    }
    trace_output = {
        "events": inference_row.get("events") or [],
        "llm_calls": inference_row.get("llm_calls") or [],
        "stop_reason": inference_row.get("stop_reason"),
    }
    attributes = [
        _string_attribute("langfuse.trace.name", "ASSERT evaluation"),
        _string_attribute("langfuse.observation.type", "span"),
        _string_attribute(
            "langfuse.observation.input",
            _json_text(trace_input),
        ),
        _string_attribute(
            "langfuse.observation.output",
            _json_text(trace_output),
        ),
        _string_attribute("langfuse.trace.metadata.assert_run_id", run_id),
        _string_attribute(
            "langfuse.trace.metadata.assert_test_case_id",
            test_case_id,
        ),
        _string_attribute("langfuse.trace.metadata.assert_test_case_type", kind),
    ]
    if behavior:
        attributes.append(
            _string_attribute("langfuse.trace.metadata.assert_behavior", behavior)
        )

    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        _string_attribute("service.name", "assert-ai"),
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {
                            "name": "assert_ai.integrations.langfuse",
                        },
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_id,
                                "name": "ASSERT evaluation",
                                "kind": 1,
                                "startTimeUnixNano": str(timestamp_ns),
                                "endTimeUnixNano": str(timestamp_ns + 1),
                                "attributes": attributes,
                                "status": {"code": 1},
                            }
                        ],
                    }
                ],
            }
        ]
    }


def verdict_dimension_to_score(
    score_row: dict[str, Any],
    *,
    dimension: str,
    trace_id: str,
) -> dict[str, Any] | None:
    """Map one successful ASSERT verdict dimension to a Langfuse score.

    ``None`` means ASSERT explicitly marked the dimension not applicable.
    """
    if infer_judge_status(score_row) != "ok":
        raise LangfuseContractError(
            "only score rows satisfying ASSERT's successful judge contract "
            "can be exported"
        )
    test_case_id = _required_string(score_row, "test_case_id", artifact="score")
    if not dimension:
        raise LangfuseContractError("score dimension must be non-empty")
    if len(trace_id) != 32 or any(char not in "0123456789abcdef" for char in trace_id):
        raise LangfuseContractError("trace_id must be a 32-character lowercase hex ID")

    score_keys = score_row.get("score_keys")
    if (
        not isinstance(score_keys, list)
        or not all(isinstance(key, str) and key for key in score_keys)
        or dimension not in score_keys
    ):
        raise LangfuseContractError(
            f"score row does not declare dimension {dimension!r} in score_keys"
        )
    verdict = score_row.get("verdict")
    dimensions = verdict.get("dimensions") if isinstance(verdict, dict) else None
    if not isinstance(dimensions, dict) or dimension not in dimensions:
        raise LangfuseContractError(
            f"score verdict is missing declared dimension {dimension!r}"
        )

    raw_value = dimensions[dimension]
    if raw_value is None:
        applicability = verdict.get("dimension_applicability")
        if isinstance(applicability, dict) and applicability.get(dimension) is False:
            return None
        raise LangfuseContractError(
            f"score dimension {dimension!r} is null without explicit non-applicability"
        )

    if isinstance(raw_value, bool):
        value: float | str = 1.0 if raw_value else 0.0
        data_type = "BOOLEAN"
    elif isinstance(raw_value, (int, float)):
        value = float(raw_value)
        data_type = "NUMERIC"
    elif isinstance(raw_value, str) and raw_value:
        value = raw_value
        data_type = "CATEGORICAL"
    else:
        raise LangfuseContractError(
            f"score dimension {dimension!r} has an unsupported value"
        )

    justifications = verdict.get("dimension_justifications")
    comment = (
        justifications.get(dimension)
        if isinstance(justifications, dict)
        and isinstance(justifications.get(dimension), str)
        else None
    )
    score_id = hashlib.sha256(
        f"ASSERT:{trace_id}:{dimension}".encode()
    ).hexdigest()[:32]
    metadata = {
        "producer": "ASSERT",
        "testCaseId": test_case_id,
    }
    judge_model = score_row.get("judge_model")
    if isinstance(judge_model, str) and judge_model:
        metadata["judgeModel"] = judge_model

    payload: dict[str, Any] = {
        "id": score_id,
        "traceId": trace_id,
        "name": dimension,
        "value": value,
        "dataType": data_type,
        "source": "API",
        "metadata": metadata,
    }
    if comment:
        payload["comment"] = comment
    return payload


def _required_string(
    row: dict[str, Any],
    key: str,
    *,
    artifact: str,
) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise LangfuseContractError(
            f"{artifact} row requires a non-empty string {key!r}"
        )
    return value


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _string_attribute(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}


__all__ = [
    "inference_to_otlp_trace",
    "trace_ids",
    "verdict_dimension_to_score",
]
