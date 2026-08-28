# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Export a completed ASSERT run's traces and judgments to Langfuse."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assert_ai.core.judge import infer_judge_status
from assert_ai.integrations.langfuse.client import LangfuseHTTPClient
from assert_ai.integrations.langfuse.errors import LangfuseContractError
from assert_ai.integrations.langfuse.mapping import (
    inference_to_otlp_trace,
    trace_ids,
    verdict_dimension_to_score,
)


@dataclass(frozen=True)
class ExportSummary:
    """Counts for one completed export."""

    run_id: str
    traces_exported: int
    scores_exported: int
    not_applicable_scores: int


@dataclass(frozen=True)
class _ExportRecord:
    trace: dict[str, Any]
    scores: tuple[dict[str, Any], ...]
    not_applicable_scores: int


class LangfuseExporter:
    """Validate local artifacts, then send traces and ASSERT-produced scores."""

    def __init__(self, client: LangfuseHTTPClient) -> None:
        self._client = client

    def export_run(
        self,
        run_dir: str | Path,
        *,
        run_id: str | None = None,
        timestamp_ns: int | None = None,
    ) -> ExportSummary:
        """Export ``inference_set.jsonl`` and ``scores.jsonl`` from ``run_dir``."""
        resolved = Path(run_dir).expanduser().resolve()
        resolved_run_id = run_id or resolved.name
        if not resolved_run_id:
            raise LangfuseContractError("run_id must be non-empty")
        inference_rows = _load_jsonl(resolved / "inference_set.jsonl")
        score_rows = _load_jsonl(resolved / "scores.jsonl")
        records = _build_export_records(
            inference_rows,
            score_rows,
            run_id=resolved_run_id,
            timestamp_ns=time.time_ns() if timestamp_ns is None else timestamp_ns,
        )

        score_count = 0
        not_applicable_count = 0
        for record in records:
            self._client.post_trace(record.trace)
            for score in record.scores:
                self._client.post_score(score)
                score_count += 1
            not_applicable_count += record.not_applicable_scores
        return ExportSummary(
            run_id=resolved_run_id,
            traces_exported=len(records),
            scores_exported=score_count,
            not_applicable_scores=not_applicable_count,
        )


def _build_export_records(
    inference_rows: list[dict[str, Any]],
    score_rows: list[dict[str, Any]],
    *,
    run_id: str,
    timestamp_ns: int,
) -> tuple[_ExportRecord, ...]:
    if not inference_rows:
        raise LangfuseContractError("inference_set.jsonl contains no rows")
    if not score_rows:
        raise LangfuseContractError("scores.jsonl contains no rows")

    scores_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for score_row in score_rows:
        key = _row_key(score_row, artifact="score")
        if key in scores_by_key:
            raise LangfuseContractError(
                f"scores.jsonl contains duplicate row {key[0]}:{key[1]}"
            )
        scores_by_key[key] = score_row

    records: list[_ExportRecord] = []
    seen_inference: set[tuple[str, str]] = set()
    for index, inference_row in enumerate(inference_rows):
        key = _row_key(inference_row, artifact="inference")
        if key in seen_inference:
            raise LangfuseContractError(
                f"inference_set.jsonl contains duplicate row {key[0]}:{key[1]}"
            )
        seen_inference.add(key)
        score_row = scores_by_key.get(key)
        if score_row is None:
            raise LangfuseContractError(
                f"inference row {key[0]}:{key[1]} has no matching score row"
            )
        if infer_judge_status(score_row) != "ok":
            raise LangfuseContractError(
                f"score row {key[0]}:{key[1]} does not satisfy ASSERT's "
                "successful judge contract"
            )
        score_keys = score_row.get("score_keys")
        if not isinstance(score_keys, list) or not all(
            isinstance(name, str) and name for name in score_keys
        ):
            raise LangfuseContractError(
                f"score row {key[0]}:{key[1]} requires string score_keys"
            )

        trace_id, _ = trace_ids(run_id=run_id, inference_row=inference_row)
        mapped_scores: list[dict[str, Any]] = []
        not_applicable = 0
        for dimension in score_keys:
            mapped = verdict_dimension_to_score(
                score_row,
                dimension=dimension,
                trace_id=trace_id,
            )
            if mapped is None:
                not_applicable += 1
            else:
                mapped_scores.append(mapped)
        records.append(
            _ExportRecord(
                trace=inference_to_otlp_trace(
                    inference_row,
                    run_id=run_id,
                    timestamp_ns=timestamp_ns + (index * 2),
                ),
                scores=tuple(mapped_scores),
                not_applicable_scores=not_applicable,
            )
        )

    unmatched = set(scores_by_key).difference(seen_inference)
    if unmatched:
        key = sorted(unmatched)[0]
        raise LangfuseContractError(
            f"score row {key[0]}:{key[1]} has no matching inference row"
        )
    return tuple(records)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise LangfuseContractError(f"required ASSERT artifact is missing: {path.name}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LangfuseContractError(
                f"{path.name}:{line_number} is not valid JSON"
            ) from exc
        if not isinstance(row, dict):
            raise LangfuseContractError(
                f"{path.name}:{line_number} must contain a JSON object"
            )
        rows.append(row)
    return rows


def _row_key(
    row: dict[str, Any],
    *,
    artifact: str,
) -> tuple[str, str]:
    kind = row.get("type")
    test_case_id = row.get("test_case_id")
    if not isinstance(kind, str) or not kind:
        raise LangfuseContractError(f"{artifact} row requires a non-empty type")
    if not isinstance(test_case_id, str) or not test_case_id:
        raise LangfuseContractError(
            f"{artifact} row requires a non-empty test_case_id"
        )
    return kind, test_case_id


__all__ = ["ExportSummary", "LangfuseExporter"]
