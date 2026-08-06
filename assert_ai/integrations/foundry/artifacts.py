# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Pure loader for a completed ASSERT run.

This module turns a run directory on disk into an ``AssertRun`` dataclass
with typed accessors for each artifact file. It is pure I/O with no
network, no Foundry types, and no dependencies on the optional
``foundry`` extra — it can be imported and unit-tested from a base
install.

The loader is intentionally forgiving: missing optional files
(``metrics.json``, ``.viewer/`` bundle, config hashes) surface as
``None`` or empty tuples rather than raising, because ASSERT skips
those under some run configurations (dry runs, ``--skip-judge``, ...).
The three files without which "the run doesn't exist" — ``config.yaml``,
``inference_set.jsonl``, ``scores.jsonl`` — raise :class:`AssertRunError`
when absent so the caller gets a clear stop signal before it tries to
POST an empty eval to Foundry.

All list-shaped artifacts (``test_set.jsonl``, ``inference_set.jsonl``,
``scores.jsonl``) are materialized as tuples of dicts. ASSERT runs are
bounded by the customer's test-case budget and always fit in memory for
export; streaming would add complexity without buying anything real
here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


class AssertRunError(ValueError):
    """Raised when a run directory is missing a required artifact.

    The exporter's contract is that a run either exists (all three of
    ``config.yaml``, ``inference_set.jsonl``, and ``scores.jsonl`` are
    present) or the caller must stop. This mirrors the existing
    ``is_run_dir`` heuristic in ``assert_ai/results.py`` but tightens
    it — the Foundry exporter cannot produce meaningful output from a
    half-finished run.
    """


# ── File names ────────────────────────────────────────────────────────
# Duplicated deliberately: ``assert_ai/core/io.py`` exports two of these
# (``INFERENCE_SET_FILE``, ``SCORES_FILE``), but the rest live scattered
# across stage modules and would drag in the full pipeline import graph.
# The exporter needs a stable local view even if a stage renames a file
# internally, so we keep the strings here.

_SUITE_TAXONOMY = "taxonomy.json"
_SUITE_SYSTEMATIZATION = "systematization.json"
_SUITE_TEST_SET = "test_set.jsonl"
_SUITE_STRATIFICATION = "stratification.json"
_SUITE_METADATA = "suite.json"
_SUITE_LATEST = "latest.json"

_RUN_CONFIG = "config.yaml"
_RUN_INFERENCE_SET = "inference_set.jsonl"
_RUN_SCORES = "scores.jsonl"
_RUN_METRICS = "metrics.json"
_RUN_MANIFEST = "manifest.json"
_RUN_ARTIFACTS = "artifacts.json"
_RUN_INFERENCE_CONFIG_HASH = ".inference_config_hash"
_RUN_JUDGE_CONFIG_HASH = ".judge_config_hash"

_VIEWER_DIR = ".viewer"
_VIEWER_FILES: tuple[str, ...] = (
    "viewer_run_manifest.json",
    "viewer_transcript_index.json",
    "viewer_audit_rows.json",
    "viewer_prompt_rows.json",
    "viewer_score_index.json",
)


@dataclass(frozen=True)
class AssertRun:
    """A fully-loaded, immutable snapshot of one ASSERT run.

    The dataclass is frozen so downstream consumers (mapper, grader
    translator, uploader) cannot mutate loader output — regressions
    where a mapper accidentally rewrites an inference row become
    ``dataclasses.FrozenInstanceError`` at call time, not silent data
    corruption in the blob container.
    """

    # ── Paths (always present) ────────────────────────────────────
    run_dir: Path
    suite_dir: Path
    suite_id: str
    run_id: str

    # ── Suite-level artifacts ────────────────────────────────────
    taxonomy: Mapping[str, Any] | None
    systematization: Mapping[str, Any] | None
    stratification: Mapping[str, Any] | None
    suite_metadata: Mapping[str, Any] | None
    latest: Mapping[str, Any] | None
    test_set: tuple[Mapping[str, Any], ...]

    # ── Run-level artifacts (required) ──────────────────────────
    config: Mapping[str, Any]
    inference_set: tuple[Mapping[str, Any], ...]
    scores: tuple[Mapping[str, Any], ...]

    # ── Run-level artifacts (optional) ──────────────────────────
    metrics: Mapping[str, Any] | None
    manifest: Mapping[str, Any] | None
    artifacts_cache: Mapping[str, Any] | None
    inference_config_hash: str | None
    judge_config_hash: str | None
    viewer_files: Mapping[str, Path] = field(default_factory=dict)

    # ── Convenience accessors ──────────────────────────────────

    @property
    def behavior_name(self) -> str | None:
        """Return the top-level behavior name from the suite taxonomy."""
        if not self.taxonomy:
            return None
        behavior = self.taxonomy.get("behavior")
        if isinstance(behavior, Mapping):
            name = behavior.get("name")
            if isinstance(name, str):
                return name
        return None

    @property
    def behavior_definition(self) -> str | None:
        """Return the top-level behavior definition from the taxonomy."""
        if not self.taxonomy:
            return None
        behavior = self.taxonomy.get("behavior")
        if isinstance(behavior, Mapping):
            definition = behavior.get("definition") or behavior.get("description")
            if isinstance(definition, str):
                return definition
        return None

    @property
    def behavior_category_count(self) -> int:
        """Return the number of behavior categories in the taxonomy."""
        if not self.taxonomy:
            return 0
        categories = self.taxonomy.get("behavior_categories")
        if isinstance(categories, list):
            return len(categories)
        return 0

    @property
    def stratification_dimension_count(self) -> int:
        """Return the number of user-defined stratification dimensions.

        Mirrors ``assert_ai.core.io.stratification_dimensions``: excludes
        metadata keys (``_*``) and the reserved ``behavior`` dimension.
        """
        if not self.stratification:
            return 0
        return sum(
            1
            for key in self.stratification
            if not key.startswith("_") and key != "behavior"
        )


# ── Loader ────────────────────────────────────────────────────────────


def load_run(run_dir: Path | str) -> AssertRun:
    """Load a completed ASSERT run from ``run_dir``.

    Raises :class:`AssertRunError` when the run directory is missing
    ``config.yaml``, ``inference_set.jsonl``, or ``scores.jsonl`` — the
    three files without which the exporter cannot produce a Foundry
    eval + run. Other missing artifacts are treated as optional.

    The suite root is inferred as ``run_dir.parent``, matching the
    ASSERT layout of ``artifacts/results/<suite>/<run>/``.
    """
    resolved = Path(run_dir).expanduser()
    if not resolved.is_dir():
        raise AssertRunError(f"Run directory does not exist: {resolved}")

    suite_dir = resolved.parent
    _require(resolved / _RUN_CONFIG)
    _require(resolved / _RUN_INFERENCE_SET)
    _require(resolved / _RUN_SCORES)

    return AssertRun(
        run_dir=resolved,
        suite_dir=suite_dir,
        suite_id=suite_dir.name,
        run_id=resolved.name,
        taxonomy=_load_json(suite_dir / _SUITE_TAXONOMY),
        systematization=_load_json(suite_dir / _SUITE_SYSTEMATIZATION),
        stratification=_load_json(suite_dir / _SUITE_STRATIFICATION),
        suite_metadata=_load_json(suite_dir / _SUITE_METADATA),
        latest=_load_json(suite_dir / _SUITE_LATEST),
        test_set=_load_jsonl(suite_dir / _SUITE_TEST_SET),
        config=_load_yaml_or_json(resolved / _RUN_CONFIG),
        inference_set=_load_jsonl(resolved / _RUN_INFERENCE_SET),
        scores=_load_jsonl(resolved / _RUN_SCORES),
        metrics=_load_json(resolved / _RUN_METRICS),
        manifest=_load_json(resolved / _RUN_MANIFEST),
        artifacts_cache=_load_json(resolved / _RUN_ARTIFACTS),
        inference_config_hash=_read_hash(resolved / _RUN_INFERENCE_CONFIG_HASH),
        judge_config_hash=_read_hash(resolved / _RUN_JUDGE_CONFIG_HASH),
        viewer_files=_collect_viewer_files(resolved / _VIEWER_DIR),
    )


# ── Internals ────────────────────────────────────────────────────────


def _require(path: Path) -> None:
    if not path.is_file():
        raise AssertRunError(
            f"Missing required artifact for Foundry export: {path.name} "
            f"(expected at {path})"
        )


def _load_json(path: Path) -> Mapping[str, Any] | None:
    """Load a JSON object; return None on missing file or non-dict payload."""
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AssertRunError(f"Malformed JSON at {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise AssertRunError(
            f"Expected a JSON object at {path}, got {type(payload).__name__}"
        )
    return payload


def _load_jsonl(path: Path) -> tuple[Mapping[str, Any], ...]:
    """Load a JSONL file into a tuple of dicts.

    Missing file returns an empty tuple. Malformed lines raise
    :class:`AssertRunError` — silently skipping them would mask
    upstream regressions that produce truncated ``scores.jsonl`` /
    ``inference_set.jsonl`` files, and Foundry has no way to signal
    "row dropped by exporter" back to the customer.
    """
    if not path.is_file():
        return ()
    rows: list[Mapping[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for lineno, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AssertRunError(
                    f"Malformed JSONL at {path}:{lineno}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise AssertRunError(
                    f"Expected an object at {path}:{lineno}, "
                    f"got {type(row).__name__}"
                )
            rows.append(row)
    return tuple(rows)


def _load_yaml_or_json(path: Path) -> Mapping[str, Any]:
    """Load ``config.yaml`` (required).

    ASSERT configs are YAML on disk; we import ``yaml`` here rather than
    at module scope so this file is importable from environments that
    don't ship PyYAML (the base install already depends on it, but
    keeping the import narrow leaves the option open).
    """
    if not path.is_file():
        raise AssertRunError(f"Missing required config file at {path}")
    text = path.read_text(encoding="utf-8")
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - yaml is a base dep
        raise AssertRunError(
            "PyYAML is required to load ASSERT config.yaml"
        ) from exc
    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise AssertRunError(f"Malformed YAML at {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise AssertRunError(
            f"Expected a mapping at {path}, got {type(payload).__name__}"
        )
    return payload


def _read_hash(path: Path) -> str | None:
    """Read a small hex hash sidecar. Missing → None; empty → None."""
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def _collect_viewer_files(viewer_dir: Path) -> Mapping[str, Path]:
    """Return an ordered mapping of known viewer read-model file paths.

    Only files listed in :data:`_VIEWER_FILES` are returned, and only
    those that exist on disk. The order matches ``_VIEWER_FILES`` so
    uploads happen in a predictable sequence.
    """
    if not viewer_dir.is_dir():
        return {}
    found: dict[str, Path] = {}
    for name in _VIEWER_FILES:
        candidate = viewer_dir / name
        if candidate.is_file():
            found[name] = candidate
    return found


# Small helper used by tests + downstream commits to iterate the
# canonical viewer file names without importing the private tuple.
def viewer_file_names() -> tuple[str, ...]:
    """Return the canonical viewer read-model file names, in upload order."""
    return _VIEWER_FILES


# Re-exported so tests and downstream commits don't reach into private
# module names for file-name constants.
SUITE_ARTIFACT_NAMES = {
    "taxonomy": _SUITE_TAXONOMY,
    "systematization": _SUITE_SYSTEMATIZATION,
    "test_set": _SUITE_TEST_SET,
    "stratification": _SUITE_STRATIFICATION,
    "suite_metadata": _SUITE_METADATA,
    "latest": _SUITE_LATEST,
}
RUN_ARTIFACT_NAMES = {
    "config": _RUN_CONFIG,
    "inference_set": _RUN_INFERENCE_SET,
    "scores": _RUN_SCORES,
    "metrics": _RUN_METRICS,
    "manifest": _RUN_MANIFEST,
    "artifacts_cache": _RUN_ARTIFACTS,
    "inference_config_hash": _RUN_INFERENCE_CONFIG_HASH,
    "judge_config_hash": _RUN_JUDGE_CONFIG_HASH,
}


__all__ = [
    "AssertRun",
    "AssertRunError",
    "load_run",
    "viewer_file_names",
    "SUITE_ARTIFACT_NAMES",
    "RUN_ARTIFACT_NAMES",
]
