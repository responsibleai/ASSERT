# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Workspace-safe, paginated access to ASSERT result artifacts."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Sequence

from assert_ai.core.io import (
    get_permissible_flag,
    load_json,
    row_behavior,
    row_factors,
)
from assert_ai.core.jsonl_index import (
    DEFAULT_MAX_INDEXED_ROW_BYTES,
    JsonlIndexError,
    JsonlIndexErrorCode,
    build_jsonl_index,
    jsonl_index_path,
    load_jsonl_index,
    scan_jsonl,
)
from assert_ai.core.judge import get_verdict_dimension, infer_judge_status
from assert_ai.services.errors import ServiceError, ServiceErrorCode
from assert_ai.services.result_metadata import (
    RUN_SUMMARY_SCHEMA_VERSION,
    SUITE_SUMMARY_SCHEMA_VERSION,
    suite_run_catalog_identity,
    suite_run_set_identity,
    write_run_summary,
    write_suite_summary,
)

if TYPE_CHECKING:
    from assert_ai.core.runtime_path_policy import RuntimePathPolicy

_CURSOR_VERSION = 1
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")


@dataclass(frozen=True, slots=True)
class RunReference:
    suite_id: str
    run_id: str

    @property
    def label(self) -> str:
        return f"{self.suite_id}/{self.run_id}"


@dataclass(frozen=True, slots=True)
class ResultPage:
    items: list[dict[str, Any]]
    next_cursor: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": self.items,
            "next_cursor": self.next_cursor,
        }


class ResultRepository:
    """Read and repair result artifacts beneath one configured results root."""

    def __init__(
        self,
        results_root: Path,
        *,
        path_policy: RuntimePathPolicy | None = None,
        default_page_size: int = 50,
        max_page_size: int = 200,
        max_page_bytes: int = 1024 * 1024,
        max_item_bytes: int = DEFAULT_MAX_INDEXED_ROW_BYTES,
    ) -> None:
        if default_page_size < 1:
            raise ValueError("default_page_size must be positive")
        if max_page_size < default_page_size:
            raise ValueError("max_page_size must be >= default_page_size")
        if max_page_bytes < 1 or max_item_bytes < 1:
            raise ValueError("result size limits must be positive")
        self.results_root = results_root.resolve()
        self.path_policy = path_policy
        if path_policy is not None:
            try:
                self.results_root = path_policy.resolve_managed_output(
                    self.results_root,
                    field_name="results root",
                    expected_root=path_policy.results_root,
                    reject_links=True,
                )
            except ValueError as exc:
                raise ServiceError(
                    ServiceErrorCode.WORKSPACE_VIOLATION,
                    str(exc),
                ) from exc
        self.default_page_size = default_page_size
        self.max_page_size = max_page_size
        self.max_page_bytes = max_page_bytes
        self.max_item_bytes = max_item_bytes

    def list_suite_catalog_entries(
        self,
        *,
        cursor: str | None = None,
        page_size: int | None = None,
    ) -> ResultPage:
        entries: list[dict[str, Any]] = []
        if self.results_root.exists():
            for suite_dir in sorted(self.results_root.iterdir()):
                if not suite_dir.is_dir() or suite_dir.name.startswith("."):
                    continue
                managed_suite_dir = self._safe_child(
                    self.results_root,
                    suite_dir.name,
                    field_name="suite",
                )
                summary = self._ensure_suite_summary(
                    managed_suite_dir,
                    verify_run_catalog=False,
                )
                if summary is not None:
                    entries.append(self._suite_catalog_entry(summary))
        entries.sort(
            key=lambda item: (
                str(item.get("created_at") or ""),
                item["suite_id"],
            ),
            reverse=True,
        )
        return self._catalog_page(
            entries,
            kind="suite_catalog",
            cursor=cursor,
            page_size=page_size,
            query={},
        )

    def get_suite(self, suite_id: str) -> dict[str, Any]:
        suite_dir = self._suite_dir(suite_id, must_exist=True)
        summary = self._ensure_suite_summary(suite_dir)
        if summary is None:
            raise ServiceError(
                ServiceErrorCode.NOT_FOUND,
                f"Suite has no readable artifacts: {suite_id}",
            )
        return self._public_summary(summary)

    def list_run_catalog_entries(
        self,
        suite_id: str,
        *,
        cursor: str | None = None,
        page_size: int | None = None,
    ) -> ResultPage:
        suite_dir = self._suite_dir(suite_id, must_exist=True)
        entries: list[dict[str, Any]] = []
        for run_dir in self._run_dirs(suite_dir):
            summary = self._ensure_run_summary(suite_dir, run_dir)
            if summary is not None:
                entries.append(self._run_catalog_entry(summary))
        entries.sort(
            key=lambda item: (
                str(
                    item.get("ended_at")
                    or item.get("updated_at")
                    or item.get("started_at")
                    or ""
                ),
                item["run_id"],
            ),
            reverse=True,
        )
        return self._catalog_page(
            entries,
            kind="run_catalog",
            cursor=cursor,
            page_size=page_size,
            query={"suite_id": suite_id},
        )

    def load_run_detail(
        self,
        suite_id: str,
        run_id: str,
        *,
        include_rows: bool = False,
    ) -> dict[str, Any]:
        suite_dir = self._suite_dir(suite_id, must_exist=True)
        run_dir = self._run_dir(suite_dir, run_id, must_exist=True)
        summary = self._ensure_run_summary(suite_dir, run_dir)
        if summary is None:
            raise ServiceError(
                ServiceErrorCode.NOT_FOUND,
                f"Run has no readable artifacts: {suite_id}/{run_id}",
            )
        detail = self._public_summary(summary)
        if include_rows:
            score_path = self._source_path(
                suite_dir=suite_dir,
                run_dir=run_dir,
                summary=summary,
                source_name="scores",
                fallback=run_dir / "scores.jsonl",
            )
            score_rows = self._all_rows(score_path) if score_path is not None else []
            detail["prompt_rows"] = [
                row for row in score_rows if not row.get("tester_model")
            ]
            detail["scenario_rows"] = [
                row for row in score_rows if row.get("tester_model")
            ]
        return detail

    def list_test_cases(
        self,
        suite_id: str,
        *,
        run_id: str | None = None,
        cursor: str | None = None,
        page_size: int | None = None,
        kind: str | None = None,
        behavior: str | None = None,
        test_case_id: str | None = None,
        factors: dict[str, Any] | None = None,
    ) -> ResultPage:
        suite_dir, run_dir, summary = self._source_context(suite_id, run_id)
        source = self._source_path(
            suite_dir=suite_dir,
            run_dir=run_dir,
            summary=summary,
            source_name="test_set",
            fallback=suite_dir / "test_set.jsonl",
        )
        query = _compact_mapping(
            {
                "suite_id": suite_id,
                "run_id": run_id,
                "kind": kind,
                "behavior": behavior,
                "test_case_id": test_case_id,
                "factors": factors or None,
            }
        )
        return self._query_jsonl(
            source,
            cursor=cursor,
            page_size=page_size,
            cursor_kind="test_cases",
            query=query,
            predicate=lambda row: self._matches_common(
                row,
                kind=kind,
                behavior=behavior,
                test_case_id=test_case_id,
                factors=factors,
            ),
        )

    def get_test_case(
        self,
        suite_id: str,
        test_case_id: str,
        *,
        kind: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        suite_dir, run_dir, summary = self._source_context(suite_id, run_id)
        source = self._source_path(
            suite_dir=suite_dir,
            run_dir=run_dir,
            summary=summary,
            source_name="test_set",
            fallback=suite_dir / "test_set.jsonl",
        )
        return self._lookup_row(
            source,
            test_case_id=test_case_id,
            kind=kind,
        )

    def list_scores(
        self,
        suite_id: str,
        run_id: str,
        *,
        cursor: str | None = None,
        page_size: int | None = None,
        kind: str | None = None,
        behavior: str | None = None,
        test_case_id: str | None = None,
        dimension: str | None = None,
        dimension_value: bool | int | str | None = None,
        match_not_applicable: bool = False,
        judge_status: str | None = None,
        target: str | None = None,
        stop_reason: str | None = None,
        has_tool_use: bool | None = None,
        factors: dict[str, Any] | None = None,
    ) -> ResultPage:
        suite_dir, run_dir, summary = self._source_context(suite_id, run_id)
        assert run_dir is not None
        source = self._source_path(
            suite_dir=suite_dir,
            run_dir=run_dir,
            summary=summary,
            source_name="scores",
            fallback=run_dir / "scores.jsonl",
        )
        inference_lookup = self._inference_lookup(
            suite_dir,
            run_dir,
            summary,
        ) if stop_reason is not None or has_tool_use is not None else None
        query = _compact_mapping(
            {
                "suite_id": suite_id,
                "run_id": run_id,
                "kind": kind,
                "behavior": behavior,
                "test_case_id": test_case_id,
                "dimension": dimension,
                "dimension_value": dimension_value,
                "match_not_applicable": match_not_applicable or None,
                "judge_status": judge_status,
                "target": target,
                "stop_reason": stop_reason,
                "has_tool_use": has_tool_use,
                "factors": factors or None,
            }
        )

        def matches(row: dict[str, Any]) -> bool:
            if not self._matches_common(
                row,
                kind=kind,
                behavior=behavior,
                test_case_id=test_case_id,
                factors=factors,
            ):
                return False
            if judge_status is not None and infer_judge_status(row) != judge_status:
                return False
            if target is not None and row.get("target") != target:
                return False
            if dimension is not None:
                value = get_verdict_dimension(row.get("verdict"), dimension)
                if match_not_applicable:
                    applicability = (
                        row.get("verdict", {}).get("dimension_applicability")
                        if isinstance(row.get("verdict"), dict)
                        else None
                    )
                    if (
                        not isinstance(applicability, dict)
                        or applicability.get(dimension) is not False
                    ):
                        return False
                elif dimension_value is None:
                    if value is None:
                        return False
                elif value != dimension_value:
                    return False
            if inference_lookup is not None:
                inference_row = inference_lookup(row)
                if inference_row is None:
                    return False
                if (
                    stop_reason is not None
                    and inference_row.get("stop_reason") != stop_reason
                ):
                    return False
                if (
                    has_tool_use is not None
                    and _row_has_tool_use(inference_row) is not has_tool_use
                ):
                    return False
            return True

        return self._query_jsonl(
            source,
            cursor=cursor,
            page_size=page_size,
            cursor_kind="scores",
            query=query,
            predicate=matches,
        )

    def list_failures(
        self,
        suite_id: str,
        run_id: str,
        *,
        dimension: str = "policy_violation",
        include_judge_failures: bool = True,
        cursor: str | None = None,
        page_size: int | None = None,
        kind: str | None = None,
        behavior: str | None = None,
    ) -> ResultPage:
        suite_dir, run_dir, summary = self._source_context(suite_id, run_id)
        assert run_dir is not None
        source = self._source_path(
            suite_dir=suite_dir,
            run_dir=run_dir,
            summary=summary,
            source_name="scores",
            fallback=run_dir / "scores.jsonl",
        )
        query = _compact_mapping(
            {
                "suite_id": suite_id,
                "run_id": run_id,
                "dimension": dimension,
                "include_judge_failures": include_judge_failures,
                "kind": kind,
                "behavior": behavior,
            }
        )

        def is_failure(row: dict[str, Any]) -> bool:
            if not self._matches_common(row, kind=kind, behavior=behavior):
                return False
            status = infer_judge_status(row)
            if include_judge_failures and status != "ok":
                return True
            return (
                status == "ok"
                and get_verdict_dimension(row.get("verdict"), dimension) is True
            )

        return self._query_jsonl(
            source,
            cursor=cursor,
            page_size=page_size,
            cursor_kind="failures",
            query=query,
            predicate=is_failure,
        )

    def get_transcript(
        self,
        suite_id: str,
        run_id: str,
        test_case_id: str,
        *,
        kind: str | None = None,
    ) -> dict[str, Any]:
        suite_dir, run_dir, summary = self._source_context(suite_id, run_id)
        assert run_dir is not None
        inference_path = self._source_path(
            suite_dir=suite_dir,
            run_dir=run_dir,
            summary=summary,
            source_name="inference_set",
            fallback=run_dir / "inference_set.jsonl",
        )
        inference = self._lookup_row(
            inference_path,
            test_case_id=test_case_id,
            kind=kind,
        )
        resolved_kind = str(inference.get("type") or kind or "")

        test_case = self._optional_lookup(
            self._source_path(
                suite_dir=suite_dir,
                run_dir=run_dir,
                summary=summary,
                source_name="test_set",
                fallback=suite_dir / "test_set.jsonl",
            ),
            test_case_id=test_case_id,
            kind=resolved_kind or None,
        )
        score = self._optional_lookup(
            self._source_path(
                suite_dir=suite_dir,
                run_dir=run_dir,
                summary=summary,
                source_name="scores",
                fallback=run_dir / "scores.jsonl",
            ),
            test_case_id=test_case_id,
            kind=resolved_kind or None,
        )
        payload = {
            "suite_id": suite_id,
            "run_id": run_id,
            "type": resolved_kind,
            "test_case_id": test_case_id,
            "test_case": test_case,
            "inference": inference,
            "score": score,
        }
        if len(_canonical_json(payload)) > self.max_item_bytes:
            raise ServiceError(
                ServiceErrorCode.ARTIFACT_TOO_LARGE,
                "Transcript response exceeds the configured item limit",
                details={"test_case_id": test_case_id},
            )
        return payload

    def compare_runs(
        self,
        run_refs: Sequence[RunReference | tuple[str, str]],
        *,
        metric: str = "policy_violation",
        behavior_limit: int = 8,
    ) -> dict[str, Any]:
        refs = [
            ref if isinstance(ref, RunReference) else RunReference(*ref)
            for ref in run_refs
        ]
        if len(refs) < 2:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "At least two runs are required for comparison",
            )
        details = [
            self.load_run_detail(ref.suite_id, ref.run_id)
            for ref in refs
        ]
        available_dimensions = _available_dimensions(details)
        if metric not in available_dimensions:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                f"Metric '{metric}' was not found in the compared runs",
                details={"available_metrics": sorted(available_dimensions)},
            )

        rows_by_run: list[list[dict[str, Any]]] = []
        inference_by_run: list[list[dict[str, Any]]] = []
        run_payloads: list[dict[str, Any]] = []
        for ref, detail in zip(refs, details, strict=True):
            suite_dir = self._suite_dir(ref.suite_id, must_exist=True)
            run_dir = self._run_dir(suite_dir, ref.run_id, must_exist=True)
            scores_path = self._source_path(
                suite_dir=suite_dir,
                run_dir=run_dir,
                summary=detail,
                source_name="scores",
                fallback=run_dir / "scores.jsonl",
            )
            inference_path = self._source_path(
                suite_dir=suite_dir,
                run_dir=run_dir,
                summary=detail,
                source_name="inference_set",
                fallback=run_dir / "inference_set.jsonl",
            )
            score_rows = self._all_rows(scores_path) if scores_path is not None else []
            inference_rows = (
                self._all_rows(inference_path)
                if inference_path is not None
                else []
            )
            rows_by_run.append(score_rows)
            inference_by_run.append(inference_rows)
            run_payloads.append(
                {
                    "label": ref.label,
                    "suite_id": ref.suite_id,
                    "run_id": ref.run_id,
                    "state": detail.get("state"),
                    "started_at": detail.get("started_at"),
                    "ended_at": detail.get("ended_at"),
                    "quality": detail.get("quality") or {},
                    "models": detail.get("models") or {},
                    "usage": (detail.get("metrics") or {}).get("totals") or {},
                    "elapsed_s": (detail.get("metrics") or {}).get("elapsed_s"),
                    "structural": _structural_summary(inference_rows),
                }
            )

        dimension_deltas = _dimension_deltas(
            refs,
            details,
            available_dimensions,
        )
        behavior_deltas: list[dict[str, Any]] = []
        requested_summary = _first_dimension_summary(details, metric)
        if not (
            isinstance(requested_summary, dict)
            and requested_summary.get("kind") == "ordinal"
        ):
            first_map = _behavior_metric_map(
                [row for row in rows_by_run[0] if not row.get("tester_model")],
                metric,
            )
            last_map = _behavior_metric_map(
                [row for row in rows_by_run[-1] if not row.get("tester_model")],
                metric,
            )
            for behavior_name in sorted(set(first_map) | set(last_map)):
                first = first_map.get(behavior_name)
                last = last_map.get(behavior_name)
                if first is None or last is None:
                    continue
                behavior_deltas.append(
                    {
                        "behavior_category": behavior_name,
                        "permissible": first.get("permissible"),
                        "first_rate": first["rate"],
                        "last_rate": last["rate"],
                        "delta": last["rate"] - first["rate"],
                        "first_count": first["count"],
                        "last_count": last["count"],
                    }
                )
            behavior_deltas.sort(
                key=lambda item: abs(float(item["delta"])),
                reverse=True,
            )
            if behavior_limit >= 0:
                behavior_deltas = behavior_deltas[:behavior_limit]

        warnings = _comparison_warnings(refs, details)
        return {
            "metric": metric,
            "baseline": refs[0].label,
            "runs": run_payloads,
            "dimension_deltas": dimension_deltas,
            "behavior_category_deltas": behavior_deltas,
            "warnings": warnings,
        }

    def _source_context(
        self,
        suite_id: str,
        run_id: str | None,
    ) -> tuple[Path, Path | None, dict[str, Any]]:
        suite_dir = self._suite_dir(suite_id, must_exist=True)
        if run_id is None:
            summary = self._ensure_suite_summary(suite_dir)
            if summary is None:
                raise ServiceError(
                    ServiceErrorCode.NOT_FOUND,
                    f"Suite has no readable artifacts: {suite_id}",
                )
            return suite_dir, None, summary
        run_dir = self._run_dir(suite_dir, run_id, must_exist=True)
        summary = self._ensure_run_summary(suite_dir, run_dir)
        if summary is None:
            raise ServiceError(
                ServiceErrorCode.NOT_FOUND,
                f"Run has no readable artifacts: {suite_id}/{run_id}",
            )
        return suite_dir, run_dir, summary

    def _ensure_suite_summary(
        self,
        suite_dir: Path,
        *,
        verify_run_catalog: bool = True,
    ) -> dict[str, Any] | None:
        summary_path = suite_dir / "suite_summary.json"
        summary = _load_optional_json(summary_path)
        if (
            isinstance(summary, dict)
            and summary.get("schema_version") == SUITE_SUMMARY_SCHEMA_VERSION
            and self._summary_sources_current(
                summary,
                suite_dir=suite_dir,
                run_dir=None,
            )
            and (
                (
                    summary.get("run_catalog_identity")
                    == suite_run_catalog_identity(suite_dir)
                )
                if verify_run_catalog
                else (
                    summary.get("run_set_identity")
                    == suite_run_set_identity(suite_dir)
                )
            )
        ):
            return summary
        if not self._suite_has_artifacts(suite_dir):
            return None
        for run_dir in self._run_dirs(suite_dir):
            self._ensure_run_summary(suite_dir, run_dir)
        ctx = self._legacy_context(suite_dir)
        return write_suite_summary(ctx, rebuild_indexes=True)

    def _ensure_run_summary(
        self,
        suite_dir: Path,
        run_dir: Path,
    ) -> dict[str, Any] | None:
        summary_path = run_dir / "run_summary.json"
        summary = _load_optional_json(summary_path)
        if (
            isinstance(summary, dict)
            and summary.get("schema_version") == RUN_SUMMARY_SCHEMA_VERSION
            and self._summary_sources_current(
                summary,
                suite_dir=suite_dir,
                run_dir=run_dir,
            )
        ):
            return summary
        if not self._run_has_artifacts(run_dir):
            return None
        ctx = self._legacy_context(suite_dir, run_dir=run_dir)
        manifest = _load_optional_json(run_dir / "manifest.json")
        if not isinstance(manifest, dict):
            scores_exist = (run_dir / "scores.jsonl").is_file()
            inference_exists = (run_dir / "inference_set.jsonl").is_file()
            stages: dict[str, str] = {}
            if inference_exists:
                stages["inference"] = "completed"
            if scores_exist:
                stages["judge"] = "completed"
            manifest = {
                "status": "completed",
                "started_at": None,
                "ended_at": None,
                "stages": stages,
            }
        return write_run_summary(
            ctx,
            manifest,
            rebuild_indexes=True,
        )

    def _legacy_context(
        self,
        suite_dir: Path,
        *,
        run_dir: Path | None = None,
    ) -> dict[str, Any]:
        latest = _load_optional_json(suite_dir / "latest.json") or {}
        refs = (
            dict(latest.get("artifacts") or {})
            if isinstance(latest, dict)
            and isinstance(latest.get("artifacts") or {}, dict)
            else {}
        )
        if run_dir is not None:
            run_artifacts = _load_optional_json(run_dir / "artifacts.json") or {}
            run_refs = (
                run_artifacts.get("artifacts")
                if isinstance(run_artifacts, dict)
                else None
            )
            if isinstance(run_refs, dict):
                refs.update(run_refs)
        refs = self._safe_artifact_refs(suite_dir, refs)

        taxonomy_path = self._artifact_ref_path(
            suite_dir,
            refs.get("systematize"),
            fallback=suite_dir / "taxonomy.json",
        )
        test_set_path = self._artifact_ref_path(
            suite_dir,
            refs.get("test_set"),
            fallback=suite_dir / "test_set.jsonl",
        )
        taxonomy = (
            _load_optional_json(taxonomy_path)
            if taxonomy_path.is_file()
            else None
        )
        behavior = (
            taxonomy.get("behavior")
            if isinstance(taxonomy, dict)
            else None
        )
        ctx: dict[str, Any] = {
            "suite_id": suite_dir.name,
            "suite_root": suite_dir,
            "run_id": run_dir.name if run_dir is not None else None,
            "run_root": run_dir,
            "taxonomy_path": taxonomy_path,
            "test_set_path": test_set_path,
            "behavior_name": (
                behavior.get("name")
                if isinstance(behavior, dict)
                else suite_dir.name
            ),
            "behavior": (
                behavior.get("description", "")
                if isinstance(behavior, dict)
                else ""
            ),
            "artifact_versions": refs,
            "path_policy": self.path_policy,
            "target": None,
            "evaluation": None,
        }
        if run_dir is not None:
            ctx["inference_set_path"] = run_dir / "inference_set.jsonl"
            ctx["scores_path"] = run_dir / "scores.jsonl"
        return ctx

    def _source_path(
        self,
        *,
        suite_dir: Path,
        run_dir: Path | None,
        summary: dict[str, Any],
        source_name: str,
        fallback: Path,
    ) -> Path | None:
        sources = summary.get("sources")
        source = sources.get(source_name) if isinstance(sources, dict) else None
        if isinstance(source, dict):
            scope = source.get("scope")
            raw_path = source.get("path")
            root = (
                run_dir
                if scope == "run"
                else suite_dir
                if scope == "suite"
                else self.path_policy.workspace_root
                if scope == "workspace" and self.path_policy is not None
                else None
            )
            if root is not None and isinstance(raw_path, str):
                candidate = self._safe_relative_path(
                    Path(root),
                    raw_path,
                    field_name=f"{source_name} source",
                )
                if candidate.is_file():
                    return candidate
        if fallback.is_file():
            return fallback
        return None

    def _summary_sources_current(
        self,
        summary: dict[str, Any],
        *,
        suite_dir: Path,
        run_dir: Path | None,
    ) -> bool:
        sources = summary.get("sources")
        if not isinstance(sources, dict):
            return True
        for source in sources.values():
            if not isinstance(source, dict):
                continue
            scope = source.get("scope")
            raw_path = source.get("path")
            expected_size = source.get("size_bytes")
            expected_mtime = source.get("mtime_ns")
            if (
                not isinstance(raw_path, str)
                or not isinstance(expected_size, int)
                or not isinstance(expected_mtime, int)
            ):
                continue
            root = (
                run_dir
                if scope == "run"
                else suite_dir
                if scope == "suite"
                else self.path_policy.workspace_root
                if scope == "workspace" and self.path_policy is not None
                else None
            )
            if root is None:
                continue
            candidate = self._safe_relative_path(
                Path(root),
                raw_path,
                field_name="summary source",
            )
            try:
                stat_result = candidate.stat()
            except FileNotFoundError:
                return False
            if (
                stat_result.st_size != expected_size
                or stat_result.st_mtime_ns != expected_mtime
            ):
                return False
        return True

    def _artifact_ref_path(
        self,
        suite_dir: Path,
        ref: Any,
        *,
        fallback: Path,
    ) -> Path:
        raw_path = ref.get("path") if isinstance(ref, dict) else None
        if isinstance(raw_path, str):
            candidate = self._safe_relative_path(
                suite_dir,
                raw_path,
                field_name="artifact reference",
            )
            if candidate.is_file():
                return candidate
        return fallback

    def _query_jsonl(
        self,
        source: Path | None,
        *,
        cursor: str | None,
        page_size: int | None,
        cursor_kind: str,
        query: dict[str, Any],
        predicate: Callable[[dict[str, Any]], bool],
    ) -> ResultPage:
        if source is None or not source.is_file():
            raise ServiceError(
                ServiceErrorCode.NOT_FOUND,
                f"Result artifact not found for {cursor_kind}",
            )
        index = self._ensure_index(source)
        source_identity = str(index["source"]["sha256"])
        query_identity = hashlib.sha256(_canonical_json(query)).hexdigest()
        start_offset = 0
        if cursor is not None:
            cursor_payload = self._decode_cursor(cursor, expected_kind=cursor_kind)
            if (
                cursor_payload.get("source_sha256") != source_identity
                or cursor_payload.get("query_sha256") != query_identity
            ):
                raise ServiceError(
                    ServiceErrorCode.STALE_CURSOR,
                    "The result source or query changed after this cursor was issued",
                )
            start_offset = cursor_payload.get("offset")
            if (
                not isinstance(start_offset, int)
                or isinstance(start_offset, bool)
                or start_offset < 0
            ):
                raise ServiceError(
                    ServiceErrorCode.INVALID_ARGUMENT,
                    "Invalid result cursor offset",
                )

        limit = self._page_size(page_size)
        ordered_items = [
            index["items"][key]
            for key in index["order"]
            if isinstance(index["items"].get(key), dict)
        ]
        items: list[dict[str, Any]] = []
        response_bytes = 2
        next_offset: int | None = None

        with source.open("rb") as handle:
            for item in ordered_items:
                offset = item.get("offset")
                if not isinstance(offset, int) or offset < start_offset:
                    continue
                length = item.get("length")
                if (
                    isinstance(length, int)
                    and length > self.max_item_bytes
                ):
                    resume_cursor = self._encode_cursor(
                        {
                            "kind": cursor_kind,
                            "source_sha256": source_identity,
                            "query_sha256": query_identity,
                            "offset": offset + length,
                        }
                    )
                    raise ServiceError(
                        ServiceErrorCode.ARTIFACT_TOO_LARGE,
                        "One result row exceeds the configured item limit",
                        details={
                            "type": item.get("type"),
                            "test_case_id": item.get("test_case_id"),
                            "size_bytes": length,
                            "resume_cursor": resume_cursor,
                        },
                    )
                row = self._read_index_item(handle, source, item)
                if not predicate(row):
                    continue
                row_size = len(_canonical_json(row))
                if items and (
                    len(items) >= limit
                    or response_bytes + row_size > self.max_page_bytes
                ):
                    next_offset = offset
                    break
                if not items and response_bytes + row_size > self.max_page_bytes:
                    row = _oversized_row_stub(
                        row,
                        size_bytes=row_size,
                    )
                    row_size = len(_canonical_json(row))
                items.append(row)
                response_bytes += row_size

        current_stat = source.stat()
        if (
            current_stat.st_size != index["source"]["size_bytes"]
            or current_stat.st_mtime_ns != index["source"]["mtime_ns"]
        ):
            raise ServiceError(
                ServiceErrorCode.STALE_CURSOR,
                "The result source changed during pagination",
            )
        next_cursor = (
            self._encode_cursor(
                {
                    "kind": cursor_kind,
                    "source_sha256": source_identity,
                    "query_sha256": query_identity,
                    "offset": next_offset,
                }
            )
            if next_offset is not None
            else None
        )
        return ResultPage(items=items, next_cursor=next_cursor)

    def _lookup_row(
        self,
        source: Path | None,
        *,
        test_case_id: str,
        kind: str | None,
    ) -> dict[str, Any]:
        if source is None or not source.is_file():
            raise ServiceError(
                ServiceErrorCode.NOT_FOUND,
                "Result artifact not found",
            )
        index = self._ensure_index(source)
        item = self._find_index_item(
            index,
            test_case_id=test_case_id,
            kind=kind,
        )
        with source.open("rb") as handle:
            row = self._read_index_item(handle, source, item)
        current_stat = source.stat()
        if (
            current_stat.st_size != index["source"]["size_bytes"]
            or current_stat.st_mtime_ns != index["source"]["mtime_ns"]
        ):
            raise ServiceError(
                ServiceErrorCode.STALE_CURSOR,
                "The result source changed during indexed lookup",
            )
        return row

    def _optional_lookup(
        self,
        source: Path | None,
        *,
        test_case_id: str,
        kind: str | None,
    ) -> dict[str, Any] | None:
        try:
            return self._lookup_row(
                source,
                test_case_id=test_case_id,
                kind=kind,
            )
        except ServiceError as exc:
            if exc.code == ServiceErrorCode.NOT_FOUND:
                return None
            raise

    def _inference_lookup(
        self,
        suite_dir: Path,
        run_dir: Path,
        summary: dict[str, Any],
    ) -> Callable[[dict[str, Any]], dict[str, Any] | None]:
        inference_path = self._source_path(
            suite_dir=suite_dir,
            run_dir=run_dir,
            summary=summary,
            source_name="inference_set",
            fallback=run_dir / "inference_set.jsonl",
        )
        inference_index = (
            self._ensure_index(inference_path)
            if inference_path is not None
            else None
        )
        cache: dict[str, dict[str, Any] | None] = {}

        def lookup(score_row: dict[str, Any]) -> dict[str, Any] | None:
            kind = score_row.get("type")
            test_case_id = score_row.get("test_case_id")
            if not isinstance(kind, str) or not isinstance(test_case_id, str):
                return None
            key = f"{kind}:{test_case_id}"
            if key not in cache:
                item = (
                    inference_index.get("items", {}).get(key)
                    if isinstance(inference_index, dict)
                    else None
                )
                if (
                    inference_path is None
                    or not isinstance(item, dict)
                ):
                    cache[key] = None
                else:
                    with inference_path.open("rb") as handle:
                        cache[key] = self._read_index_item(
                            handle,
                            inference_path,
                            item,
                        )
            return cache[key]

        return lookup

    def _ensure_index(self, source: Path) -> dict[str, Any]:
        try:
            return load_jsonl_index(source)
        except JsonlIndexError as exc:
            if exc.code not in {
                JsonlIndexErrorCode.INVALID_INDEX,
                JsonlIndexErrorCode.STALE_INDEX,
            }:
                raise self._jsonl_service_error(exc) from exc
        try:
            scan = scan_jsonl(
                source,
                allow_trailing_partial=_allows_trailing_partial(source),
            )
            return build_jsonl_index(source, scan=scan)
        except JsonlIndexError as exc:
            raise self._jsonl_service_error(exc) from exc

    def _read_index_item(
        self,
        handle: Any,
        source: Path,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        offset = item.get("offset")
        length = item.get("length")
        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
            or not isinstance(length, int)
            or isinstance(length, bool)
            or length < 1
        ):
            raise ServiceError(
                ServiceErrorCode.RUN_FAILED,
                f"Invalid JSONL index byte range for {source.name}",
            )
        if length > self.max_item_bytes:
            raise ServiceError(
                ServiceErrorCode.ARTIFACT_TOO_LARGE,
                "Indexed result row exceeds the configured item limit",
                details={
                    "type": item.get("type"),
                    "test_case_id": item.get("test_case_id"),
                },
            )
        handle.seek(offset)
        raw = handle.read(length)
        try:
            row = json.loads(raw.strip())
        except json.JSONDecodeError as exc:
            raise ServiceError(
                ServiceErrorCode.STALE_CURSOR,
                f"Indexed row is no longer readable in {source.name}",
            ) from exc
        if not isinstance(row, dict):
            raise ServiceError(
                ServiceErrorCode.STALE_CURSOR,
                f"Indexed row is no longer an object in {source.name}",
            )
        if (
            row.get("type") != item.get("type")
            or row.get("test_case_id") != item.get("test_case_id")
        ):
            raise ServiceError(
                ServiceErrorCode.STALE_CURSOR,
                f"Indexed row identity changed in {source.name}",
            )
        return row

    def _find_index_item(
        self,
        index: dict[str, Any],
        *,
        test_case_id: str,
        kind: str | None,
    ) -> dict[str, Any]:
        if kind is not None:
            exact = index.get("items", {}).get(f"{kind}:{test_case_id}")
            if isinstance(exact, dict):
                return exact
        matches = [
            item
            for item in index.get("items", {}).values()
            if isinstance(item, dict)
            and item.get("test_case_id") == test_case_id
            and (kind is None or item.get("type") == kind)
        ]
        if not matches:
            raise ServiceError(
                ServiceErrorCode.NOT_FOUND,
                f"Test case not found: {test_case_id}",
            )
        if len(matches) > 1:
            raise ServiceError(
                ServiceErrorCode.CONFLICT,
                f"Test case ID is ambiguous; provide its type: {test_case_id}",
            )
        return matches[0]

    def _all_rows(self, source: Path | None) -> list[dict[str, Any]]:
        if source is None or not source.is_file():
            return []
        try:
            return [
                record.row
                for record in scan_jsonl(
                    source,
                    allow_trailing_partial=_allows_trailing_partial(source),
                ).records
            ]
        except JsonlIndexError as exc:
            raise self._jsonl_service_error(exc) from exc

    def _catalog_page(
        self,
        entries: list[dict[str, Any]],
        *,
        kind: str,
        cursor: str | None,
        page_size: int | None,
        query: dict[str, Any],
    ) -> ResultPage:
        identity = hashlib.sha256(_canonical_json(entries)).hexdigest()
        query_identity = hashlib.sha256(_canonical_json(query)).hexdigest()
        offset = 0
        if cursor is not None:
            payload = self._decode_cursor(cursor, expected_kind=kind)
            if (
                payload.get("catalog_sha256") != identity
                or payload.get("query_sha256") != query_identity
            ):
                raise ServiceError(
                    ServiceErrorCode.STALE_CURSOR,
                    "The result catalog changed after this cursor was issued",
                )
            offset = payload.get("offset")
            if (
                not isinstance(offset, int)
                or isinstance(offset, bool)
                or offset < 0
                or offset > len(entries)
            ):
                raise ServiceError(
                    ServiceErrorCode.INVALID_ARGUMENT,
                    "Invalid catalog cursor offset",
                )
        limit = self._page_size(page_size)
        page_items = entries[offset : offset + limit]
        next_offset = offset + len(page_items)
        next_cursor = (
            self._encode_cursor(
                {
                    "kind": kind,
                    "catalog_sha256": identity,
                    "query_sha256": query_identity,
                    "offset": next_offset,
                }
            )
            if next_offset < len(entries)
            else None
        )
        return ResultPage(items=page_items, next_cursor=next_cursor)

    def _suite_catalog_entry(
        self,
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        behavior = summary.get("behavior")
        counts = summary.get("test_case_counts")
        return {
            "suite_id": summary.get("suite_id"),
            "status": summary.get("status"),
            "behavior_name": (
                behavior.get("name")
                if isinstance(behavior, dict)
                else None
            ),
            "behavior_category_count": int(
                summary.get("behavior_category_count") or 0
            ),
            "prompt_test_case_count": int(
                (counts or {}).get("prompt") or 0
            ),
            "scenario_test_case_count": int(
                (counts or {}).get("scenario") or 0
            ),
            "run_count": int(summary.get("run_count") or 0),
            "created_at": summary.get("created_at"),
            "updated_at": summary.get("updated_at"),
            "latest_run": summary.get("latest_run"),
        }

    def _run_catalog_entry(
        self,
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        quality = summary.get("quality") or {}
        return {
            "suite_id": summary.get("suite_id"),
            "run_id": summary.get("run_id"),
            "status": summary.get("state"),
            "current_stage": summary.get("current_stage"),
            "started_at": summary.get("started_at"),
            "ended_at": summary.get("ended_at"),
            "updated_at": summary.get("updated_at"),
            "prompt_metrics": quality.get("prompt"),
            "scenario_metrics": quality.get("scenario"),
            "models": summary.get("models") or {},
            "counts": summary.get("counts") or {},
            "metrics": summary.get("metrics"),
        }

    def _matches_common(
        self,
        row: dict[str, Any],
        *,
        kind: str | None = None,
        behavior: str | None = None,
        test_case_id: str | None = None,
        factors: dict[str, Any] | None = None,
    ) -> bool:
        if kind is not None and row.get("type") != kind:
            return False
        if test_case_id is not None and row.get("test_case_id") != test_case_id:
            return False
        if behavior is not None and row_behavior(row) != behavior:
            return False
        if factors:
            row_dimensions = row_factors(row)
            if any(row_dimensions.get(key) != value for key, value in factors.items()):
                return False
        return True

    def _suite_dir(self, suite_id: str, *, must_exist: bool) -> Path:
        self._validate_identifier(suite_id, "suite_id")
        candidate = self._safe_child(
            self.results_root,
            suite_id,
            field_name="suite",
        )
        if must_exist and not candidate.is_dir():
            raise ServiceError(
                ServiceErrorCode.NOT_FOUND,
                f"Suite not found: {suite_id}",
            )
        return candidate

    def _run_dir(
        self,
        suite_dir: Path,
        run_id: str,
        *,
        must_exist: bool,
    ) -> Path:
        self._validate_identifier(run_id, "run_id")
        candidate = self._safe_child(
            suite_dir,
            run_id,
            field_name="run",
        )
        if must_exist and not candidate.is_dir():
            raise ServiceError(
                ServiceErrorCode.NOT_FOUND,
                f"Run not found: {suite_dir.name}/{run_id}",
            )
        return candidate

    def _safe_child(
        self,
        root: Path,
        name: str,
        *,
        field_name: str,
    ) -> Path:
        candidate = root / name
        if self.path_policy is not None:
            try:
                return self.path_policy.resolve_managed_output(
                    candidate,
                    field_name=field_name,
                    expected_root=root,
                    reject_links=True,
                )
            except ValueError as exc:
                raise ServiceError(
                    ServiceErrorCode.WORKSPACE_VIOLATION,
                    str(exc),
                ) from exc
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError as exc:
            raise ServiceError(
                ServiceErrorCode.WORKSPACE_VIOLATION,
                f"{field_name} escapes the results root",
            ) from exc
        return resolved

    def _safe_relative_path(
        self,
        root: Path,
        raw_path: str,
        *,
        field_name: str,
    ) -> Path:
        candidate = Path(raw_path)
        if candidate.is_absolute():
            raise ServiceError(
                ServiceErrorCode.WORKSPACE_VIOLATION,
                f"{field_name} must be relative",
            )
        resolved = (root / candidate).resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError as exc:
            raise ServiceError(
                ServiceErrorCode.WORKSPACE_VIOLATION,
                f"{field_name} escapes its managed root",
            ) from exc
        if self.path_policy is not None:
            try:
                self.path_policy.require_managed_tree(
                    resolved,
                    field_name=field_name,
                    expected_root=root,
                )
            except ValueError as exc:
                raise ServiceError(
                    ServiceErrorCode.WORKSPACE_VIOLATION,
                    str(exc),
                ) from exc
        return resolved

    def _run_dirs(self, suite_dir: Path) -> list[Path]:
        run_dirs: list[Path] = []
        for child in sorted(suite_dir.iterdir()):
            if (
                not child.is_dir()
                or child.name == "artifacts"
                or child.name.startswith(".")
            ):
                continue
            managed_child = self._safe_child(
                suite_dir,
                child.name,
                field_name="run",
            )
            if managed_child.is_dir() and self._run_has_artifacts(managed_child):
                run_dirs.append(managed_child)
        return run_dirs

    @staticmethod
    def _suite_has_artifacts(suite_dir: Path) -> bool:
        return any(
            (suite_dir / filename).exists()
            for filename in (
                "suite.json",
                "suite_summary.json",
                "taxonomy.json",
                "test_set.jsonl",
                "latest.json",
            )
        )

    @staticmethod
    def _run_has_artifacts(run_dir: Path) -> bool:
        return any(
            (run_dir / filename).exists()
            for filename in (
                "run_summary.json",
                "manifest.json",
                "inference_set.jsonl",
                "scores.jsonl",
            )
        )

    @staticmethod
    def _validate_identifier(value: str, field_name: str) -> None:
        if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                f"{field_name} must be a safe result identifier",
            )

    def _page_size(self, page_size: int | None) -> int:
        value = self.default_page_size if page_size is None else page_size
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 1
            or value > self.max_page_size
        ):
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                f"page_size must be between 1 and {self.max_page_size}",
            )
        return value

    @staticmethod
    def _encode_cursor(payload: dict[str, Any]) -> str:
        body = {"version": _CURSOR_VERSION, **payload}
        return base64.urlsafe_b64encode(_canonical_json(body)).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(
        cursor: str,
        *,
        expected_kind: str,
    ) -> dict[str, Any]:
        try:
            padding = "=" * (-len(cursor) % 4)
            decoded = base64.urlsafe_b64decode(cursor + padding)
            payload = json.loads(decoded)
        except (
            ValueError,
            json.JSONDecodeError,
            binascii.Error,
            UnicodeError,
        ) as exc:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "Invalid result cursor",
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("version") != _CURSOR_VERSION
            or payload.get("kind") != expected_kind
        ):
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "Result cursor is not valid for this query",
            )
        return payload

    def _safe_artifact_refs(
        self,
        suite_dir: Path,
        refs: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        safe_refs: dict[str, dict[str, Any]] = {}
        for stage_name, raw_ref in refs.items():
            if not isinstance(stage_name, str) or not isinstance(raw_ref, dict):
                continue
            safe_ref: dict[str, Any] = {}
            for key in (
                "artifact_type",
                "version",
                "input_hash",
                "config_hash",
                "behavior_hash",
            ):
                value = raw_ref.get(key)
                if isinstance(value, (str, int, float, bool)) or value is None:
                    safe_ref[key] = value
            for key in ("path", "artifact_dir", "metadata_path"):
                value = raw_ref.get(key)
                if not isinstance(value, str):
                    continue
                try:
                    self._safe_relative_path(
                        suite_dir,
                        value,
                        field_name=f"{stage_name} artifact {key}",
                    )
                except ServiceError:
                    continue
                safe_ref[key] = Path(value).as_posix()
            file_hashes = raw_ref.get("file_hashes")
            if isinstance(file_hashes, dict):
                safe_ref["file_hashes"] = {
                    str(key): str(value)
                    for key, value in file_hashes.items()
                    if isinstance(key, str) and isinstance(value, str)
                }
            safe_refs[stage_name] = safe_ref
        return safe_refs

    @staticmethod
    def _public_summary(summary: dict[str, Any]) -> dict[str, Any]:
        """Return a detached JSON-compatible payload."""
        payload = json.loads(json.dumps(summary, ensure_ascii=False))
        assert isinstance(payload, dict)
        return payload

    @staticmethod
    def _jsonl_service_error(exc: JsonlIndexError) -> ServiceError:
        if exc.code == JsonlIndexErrorCode.NOT_FOUND:
            code = ServiceErrorCode.NOT_FOUND
        elif exc.code == JsonlIndexErrorCode.ROW_TOO_LARGE:
            code = ServiceErrorCode.ARTIFACT_TOO_LARGE
        else:
            code = ServiceErrorCode.RUN_FAILED
        return ServiceError(
            code,
            str(exc),
            details={
                "jsonl_error_code": exc.code.value,
                "line_number": exc.line_number,
            },
        )


def _compact_mapping(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in values.items()
        if value is not None
    }


def _allows_trailing_partial(path: Path) -> bool:
    return path.name in {"inference_set.jsonl", "scores.jsonl"}


def _load_optional_json(path: Path) -> dict[str, Any] | None:
    try:
        return load_json(path)
    except (OSError, ValueError):
        return None


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _row_has_tool_use(row: dict[str, Any]) -> bool:
    events = row.get("events")
    if not isinstance(events, list):
        return False
    return any(
        isinstance(event, dict)
        and isinstance(event.get("edit"), dict)
        and event["edit"].get("type") in {"tool_call", "tool_result"}
        for event in events
    )


def _oversized_row_stub(
    row: dict[str, Any],
    *,
    size_bytes: int,
) -> dict[str, Any]:
    return {
        key: row[key]
        for key in (
            "type",
            "test_case_id",
            "behavior",
            "target",
            "tester_model",
            "judge_model",
            "judge_status",
            "stop_reason",
        )
        if key in row
    } | {
        "content_omitted": True,
        "size_bytes": size_bytes,
        "retrieval_hint": (
            "Use get_test_case or get_transcript for this item."
        ),
    }


def _behavior_metric_map(
    rows: Iterable[dict[str, Any]],
    metric: str,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if infer_judge_status(row) != "ok":
            continue
        value = get_verdict_dimension(row.get("verdict"), metric)
        if not isinstance(value, bool):
            continue
        behavior = row_behavior(row)
        bucket = grouped.setdefault(
            behavior,
            {
                "true_count": 0,
                "count": 0,
                "permissible": get_permissible_flag(row),
            },
        )
        bucket["true_count"] += int(value)
        bucket["count"] += 1
    return {
        behavior: {
            "rate": bucket["true_count"] / bucket["count"],
            "count": bucket["count"],
            "permissible": bucket["permissible"],
        }
        for behavior, bucket in grouped.items()
        if bucket["count"] > 0
    }


def _structural_summary(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    row_count = 0
    total_events = 0
    message_events = 0
    tool_events = 0
    rows_with_tools = 0
    rows_with_traces = 0
    for row in rows:
        row_count += 1
        events = row.get("events")
        if not isinstance(events, list):
            events = []
        total_events += len(events)
        row_has_tools = False
        for event in events:
            edit = event.get("edit") if isinstance(event, dict) else None
            edit_type = edit.get("type") if isinstance(edit, dict) else None
            if edit_type == "add_message":
                message_events += 1
            if edit_type in {"tool_call", "tool_result"}:
                tool_events += 1
                row_has_tools = True
        rows_with_tools += int(row_has_tools)
        rows_with_traces += int(
            any(
                key in row
                for key in (
                    "trace_id",
                    "span_id",
                    "trace",
                    "trace_refs",
                    "otel_trace",
                )
            )
        )
    return {
        "inference_rows": row_count,
        "total_events": total_events,
        "message_events": message_events,
        "tool_events": tool_events,
        "rows_with_tools": rows_with_tools,
        "rows_with_traces": rows_with_traces,
    }


def _available_dimensions(details: Iterable[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for detail in details:
        quality = detail.get("quality")
        if not isinstance(quality, dict):
            continue
        for kind in ("prompt", "scenario"):
            metrics = quality.get(kind)
            dimensions = (
                metrics.get("dimensions")
                if isinstance(metrics, dict)
                else None
            )
            if isinstance(dimensions, dict):
                names.update(str(name) for name in dimensions)
    return names


def _first_dimension_summary(
    details: Iterable[dict[str, Any]],
    dimension: str,
) -> dict[str, Any] | None:
    for detail in details:
        quality = detail.get("quality")
        if not isinstance(quality, dict):
            continue
        for kind in ("prompt", "scenario"):
            metrics = quality.get(kind)
            dimensions = (
                metrics.get("dimensions")
                if isinstance(metrics, dict)
                else None
            )
            summary = (
                dimensions.get(dimension)
                if isinstance(dimensions, dict)
                else None
            )
            if isinstance(summary, dict):
                return summary
    return None


def _dimension_deltas(
    refs: Sequence[RunReference],
    details: Sequence[dict[str, Any]],
    dimensions: set[str],
) -> dict[str, Any]:
    baseline = details[0]
    payload: dict[str, Any] = {}
    for dimension in sorted(dimensions):
        first_summary = _dimension_summary_by_kind(baseline, dimension)
        kind = next(
            (
                summary.get("kind")
                for summary in first_summary.values()
                if isinstance(summary, dict) and summary.get("kind")
            ),
            "binary",
        )
        runs: list[dict[str, Any]] = []
        for ref, detail in zip(refs, details, strict=True):
            summaries = _dimension_summary_by_kind(detail, dimension)
            row: dict[str, Any] = {
                "label": ref.label,
                "prompt": summaries.get("prompt"),
                "scenario": summaries.get("scenario"),
            }
            if kind != "ordinal":
                row["prompt_rate_delta"] = _rate_delta(
                    summaries.get("prompt"),
                    first_summary.get("prompt"),
                )
                row["scenario_rate_delta"] = _rate_delta(
                    summaries.get("scenario"),
                    first_summary.get("scenario"),
                )
            else:
                row["prompt_distribution_delta"] = _distribution_delta(
                    summaries.get("prompt"),
                    first_summary.get("prompt"),
                )
                row["scenario_distribution_delta"] = _distribution_delta(
                    summaries.get("scenario"),
                    first_summary.get("scenario"),
                )
            runs.append(row)
        payload[dimension] = {"kind": kind, "runs": runs}
    return payload


def _dimension_summary_by_kind(
    detail: dict[str, Any],
    dimension: str,
) -> dict[str, dict[str, Any] | None]:
    quality = detail.get("quality")
    result: dict[str, dict[str, Any] | None] = {}
    for kind in ("prompt", "scenario"):
        metrics = quality.get(kind) if isinstance(quality, dict) else None
        dimensions = (
            metrics.get("dimensions")
            if isinstance(metrics, dict)
            else None
        )
        summary = (
            dimensions.get(dimension)
            if isinstance(dimensions, dict)
            else None
        )
        result[kind] = summary if isinstance(summary, dict) else None
    return result


def _rate_delta(
    current: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
) -> float | None:
    current_rate = current.get("rate") if isinstance(current, dict) else None
    baseline_rate = baseline.get("rate") if isinstance(baseline, dict) else None
    if not isinstance(current_rate, (int, float)) or not isinstance(
        baseline_rate,
        (int, float),
    ):
        return None
    return float(current_rate) - float(baseline_rate)


def _distribution_delta(
    current: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
) -> dict[str, float]:
    current_rates = current.get("rates") if isinstance(current, dict) else None
    baseline_rates = baseline.get("rates") if isinstance(baseline, dict) else None
    if not isinstance(current_rates, dict) or not isinstance(baseline_rates, dict):
        return {}
    return {
        str(grade): float(current_rates.get(grade, 0.0))
        - float(baseline_rates.get(grade, 0.0))
        for grade in sorted(set(current_rates) | set(baseline_rates))
    }


def _comparison_warnings(
    refs: Sequence[RunReference],
    details: Sequence[dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    prompt_sizes = {
        int(
            (((detail.get("quality") or {}).get("prompt") or {}).get("total"))
            or 0
        )
        for detail in details
    }
    scenario_sizes = {
        int(
            (((detail.get("quality") or {}).get("scenario") or {}).get("total"))
            or 0
        )
        for detail in details
    }
    if len(prompt_sizes) > 1 or len(scenario_sizes) > 1:
        warnings.append("Compared runs have different prompt or scenario sample sizes.")

    test_set_hashes = {
        str(
            (((detail.get("sources") or {}).get("test_set") or {}).get("sha256"))
            or ""
        )
        for detail in details
    }
    if len(test_set_hashes - {""}) > 1:
        warnings.append("Compared runs reference different test-set source hashes.")

    targets = {
        str(
            (((detail.get("models") or {}).get("target") or {}).get("identifier"))
            or ""
        )
        for detail in details
    }
    if len(targets - {""}) > 1:
        warnings.append("Compared runs use different targets.")

    if len({ref.suite_id for ref in refs}) > 1:
        warnings.append("Cross-suite comparison may include different behavior taxonomies.")
    return warnings
