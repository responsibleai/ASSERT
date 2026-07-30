# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Path, JSON, and JSONL helpers used across ASSERT workflows."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from importlib.resources import files as _resource_files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, Dict, Iterable


log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]

# Version of the primary artifact contracts: taxonomy.json, test_set.jsonl,
# inference_set.jsonl, scores.jsonl, manifest.json. Bump when a change would
# make an older reader misinterpret an artifact rather than merely miss a field.
ARTIFACT_SCHEMA_VERSION = 1

_SCHEMA_SIDECAR_SUFFIX = ".schema.json"


def write_artifact_schema(
    artifact_path: Path,
    *,
    artifact: str,
    version: int = ARTIFACT_SCHEMA_VERSION,
) -> Path:
    """Record the schema version of a JSONL artifact in a sidecar file.

    Only ``metrics.json`` carried a ``schema_version``; the primary artifacts did
    not, so one produced by a newer ASSERT is consumed silently by an older
    viewer or analysis script. For a tool whose output is meant to be durable
    evaluation evidence, the artifacts should say what they are.

    A sidecar is used rather than a header line inside the JSONL. A header would
    be read as a data record by any reader that does not know about it -
    including the previous version of ASSERT - which turns a version stamp into
    a corrupt first row. A sidecar is ignored harmlessly instead.
    """
    sidecar = artifact_path.with_name(artifact_path.name + _SCHEMA_SIDECAR_SUFFIX)
    write_json(
        sidecar,
        {"artifact": artifact, "schema_version": version},
    )
    return sidecar


def read_artifact_schema_version(artifact_path: Path) -> int | None:
    """Return the recorded schema version for an artifact, if there is one."""
    sidecar = artifact_path.with_name(artifact_path.name + _SCHEMA_SIDECAR_SUFFIX)
    if not sidecar.exists():
        return None
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = payload.get("schema_version") if isinstance(payload, dict) else None
    return version if isinstance(version, int) else None


def check_artifact_schema(artifact_path: Path) -> bool:
    """Warn when an artifact was written by an incompatible ASSERT version.

    Returns True when the artifact is safe to read. An artifact with no recorded
    version predates the stamp and is assumed compatible, so existing runs keep
    working.
    """
    version = read_artifact_schema_version(artifact_path)
    if version is None or version == ARTIFACT_SCHEMA_VERSION:
        return True
    if version > ARTIFACT_SCHEMA_VERSION:
        log.warning(
            "%s was written with artifact schema v%d but this ASSERT understands "
            "v%d. Fields may be missing or mean something different; read the "
            "results with care.",
            artifact_path.name, version, ARTIFACT_SCHEMA_VERSION,
        )
    else:
        log.warning(
            "%s uses artifact schema v%d, older than this ASSERT's v%d.",
            artifact_path.name, version, ARTIFACT_SCHEMA_VERSION,
        )
    return False


def archive_artifact(path: Path, *, reason: str) -> Path | None:
    """Move a stale artifact aside instead of deleting it.

    Resume and ``--force-stage`` previously called ``unlink()`` on
    ``inference_set.jsonl`` and ``scores.jsonl``. Those files are the evaluation
    evidence - every transcript, every verdict - and a config hash mismatch is
    not always the operator's intention. Deleting them outright means an
    accidental edit costs a full re-run, and the fact that anything was
    destroyed is not recorded anywhere.

    The file is renamed to ``<name>.<UTC timestamp>.bak`` beside the original
    and the new path is logged. Returns the backup path, or ``None`` when the
    file did not exist.

    Backups are never pruned automatically, because pruning evidence is the
    behaviour being fixed. Set ``ASSERT_DISCARD_STALE_ARTIFACTS=1`` to restore
    the previous delete-outright behaviour.
    """
    if not path.exists():
        return None

    if os.environ.get("ASSERT_DISCARD_STALE_ARTIFACTS", "").lower() in ("1", "true", "yes"):
        path.unlink()
        log.info("Discarded %s (%s); ASSERT_DISCARD_STALE_ARTIFACTS is set", path, reason)
        return None

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    backup = path.with_name(f"{path.name}.{stamp}.bak")
    suffix = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.{stamp}.{suffix}.bak")
        suffix += 1

    try:
        path.rename(backup)
    except OSError as e:
        # Preserving the file is best-effort. Failing the stage because a backup
        # could not be written would be worse than proceeding, but the operator
        # needs to know the evidence is about to go.
        log.warning(
            "Could not preserve %s as a backup (%s); removing it instead (%s)",
            path, e, reason,
        )
        path.unlink(missing_ok=True)
        return None

    log.info("Preserved %s as %s (%s)", path.name, backup.name, reason)
    return backup


def resolve_path(path: str | Path) -> Path:
    """Resolve relative paths against CWD, then repo root."""
    p = Path(path).expanduser()
    if p.is_absolute():
        return p
    cwd_candidate = Path.cwd() / p
    if cwd_candidate.exists():
        return cwd_candidate
    return BASE_DIR / p


def write_json(path: Path, payload: Any) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    _atomic_write_text(path, text)


def append_jsonl_row(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            tmp_name = handle.name
        os.replace(tmp_name, path)
        tmp_name = None
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass


def load_test_cases(
    path: str | Path,
    *,
    strict: bool = False,
) -> list[dict[str, Any]]:
    """Load test case records from a JSONL file."""
    resolved = resolve_path(path)
    if not resolved.is_file():
        tried = [str(path), str(resolved)]
        raise FileNotFoundError(f"Test set file not found. Tried: {tried}")

    records: list[dict[str, Any]] = []
    bad_lines: list[int] = []
    for lineno, line in _iter_nonempty_lines(resolved):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            if strict:
                raise ValueError(
                    f"Malformed JSON at line {lineno} in {resolved}: {line[:120]}"
                )
            bad_lines.append(lineno)
    if bad_lines:
        log.warning(
            "Skipped %d malformed line(s) in %s (lines: %s)",
            len(bad_lines), resolved, bad_lines[:10],
        )
    return records


def normalize_test_case_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign canonical opaque test case IDs."""
    normalized = [dict(row) for row in rows]
    counter = 1
    for row in normalized:
        row["test_case_id"] = f"test_case_{counter:06d}"
        counter += 1
    return normalized


def slugify(text: str) -> str:
    """Collapse free text into a filesystem-friendly slug."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load JSONL records from disk. Returns empty list if file missing."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for i, line in _iter_nonempty_lines(path):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            log.warning("%s:%d: %s", path, i, exc)
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def load_json(path: Path) -> dict[str, Any] | None:
    """Load a JSON file. Returns None if missing or not a dict."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    return data if isinstance(data, dict) else None


# ── Prompt loading ─────────────────────────────────────────────

# Resolve prompts via importlib.resources so they load correctly from an
# installed wheel (where the package may live anywhere on sys.path) and not
# only from a repo checkout. Returns a Traversable, which supports ``/`` and
# ``read_text``/``read_bytes``/``is_file`` like a Path.
PROMPTS_DIR: Traversable = _resource_files("assert_ai.internal_pipeline_prompts")


def load_prompt_text(filename: str) -> str:
    """Load a prompt file shipped inside the assert_ai package."""
    resource = PROMPTS_DIR / filename
    if not resource.is_file():
        raise FileNotFoundError(f"Prompt file not found: {resource}")
    return resource.read_text(encoding="utf-8")


def normalize_test_case_context(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


# ── Item helpers ───────────────────────────────────────────────

def get_permissible_flag(payload: dict[str, Any], default: bool | None = None) -> bool | None:
    """Read the canonical permissibility flag."""
    raw = payload.get("permissible")
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.lower() not in ("false", "0", "no", "")
    return bool(raw)


# ── Output filenames (written by run stages, read by viewer) ──

INFERENCE_SET_FILE = "inference_set.jsonl"
SCORES_FILE = "scores.jsonl"

# ── Stratification helpers ────────────────────────────────────────────


def stratification_dimensions(stratification: dict[str, Any]) -> tuple[str, ...]:
    """Return user-defined stratification dimensions in stable order.

    Excludes metadata keys and the reserved ``behavior`` dimension.
    """
    return tuple(
        key for key in stratification if not key.startswith("_") and key != "behavior"
    )

STRATIFICATION_FILE = "stratification.json"


# ── Template rendering ────────────────────────────────────────────


def fill_template(template: str, replacements: dict[str, str]) -> str:
    """Replace ``{{placeholders}}`` in *template*; error on leftovers."""
    required = set(re.findall(r"\{\{(\w+)\}\}", template))
    missing = required.difference(replacements)
    if missing:
        raise ValueError(
            f"unreplaced template placeholders: {', '.join(sorted(missing))}"
        )
    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    return rendered


# ── Taxonomy loading ────────────────────────────────────────────────


def load_policy(path: str | Path) -> dict[str, Any]:
    """Load and normalize a taxonomy JSON file."""
    taxonomy = json.loads(resolve_path(path).read_text(encoding="utf-8"))
    for behavior in taxonomy.get("behavior_categories", []):
        permissible = get_permissible_flag(behavior)
        if permissible is not None:
            behavior["permissible"] = permissible
    return taxonomy


def permissible_by_behavior(taxonomy: dict[str, Any] | None) -> dict[str, bool]:
    """Return canonical permissibility flags keyed by behavior name."""
    behavior_categories = (taxonomy or {}).get("behavior_categories")
    if not isinstance(behavior_categories, list):
        return {}
    return {
        str(entry.get("name") or ""): bool(entry.get("permissible"))
        for entry in behavior_categories
        if isinstance(entry, dict) and str(entry.get("name") or "")
    }


def definitions_by_behavior(taxonomy: dict[str, Any] | None) -> dict[str, str]:
    """Return canonical behavior definitions keyed by behavior name."""
    behavior_categories = (taxonomy or {}).get("behavior_categories")
    if not isinstance(behavior_categories, list):
        return {}
    return {
        str(entry.get("name") or ""): str(entry.get("definition") or "")
        for entry in behavior_categories
        if isinstance(entry, dict) and str(entry.get("name") or "")
    }


def policy_definition(
    policy_definition_by_name: dict[str, str],
    behavior_name: str,
) -> str:
    """Return a behavior's taxonomy definition or raise on missing taxonomy."""
    try:
        return policy_definition_by_name[behavior_name]
    except KeyError as exc:
        raise ValueError(
            f"behavior '{behavior_name}' is missing from taxonomy.behavior_categories"
        ) from exc


def policy_permissible(
    policy_permissible_by_name: dict[str, bool],
    behavior_name: str,
) -> bool:
    """Return a behavior's taxonomy permissibility or raise on missing taxonomy."""
    try:
        return policy_permissible_by_name[behavior_name]
    except KeyError as exc:
        raise ValueError(
            f"behavior '{behavior_name}' is missing from taxonomy.behavior_categories"
        ) from exc


def row_behavior(row: dict[str, Any]) -> str:
    """Return behavior name from a row's dimensions, or empty string if absent.

    Test set/transcript/score rows carry behavior inside `dimensions`; this is the
    single, canonical accessor used everywhere downstream.
    """
    dimensions = row.get("dimensions")
    if not isinstance(dimensions, dict):
        return ""
    value = dimensions.get("behavior")
    return str(value) if value else ""


def row_factors(row: dict[str, Any]) -> dict[str, str] | None:
    """Return the row's `dimensions` dict if present and well-formed, else None."""
    dimensions = row.get("dimensions")
    return dimensions if isinstance(dimensions, dict) else None


def _iter_nonempty_lines(path: Path) -> Iterable[tuple[int, str]]:
    with open(path, encoding="utf-8") as handle:
        for lineno, raw_line in enumerate(handle, 1):
            line = raw_line.strip()
            if line:
                yield lineno, line
