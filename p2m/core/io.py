"""Path, JSON, and JSONL helpers used across p2m workflows."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable


log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]


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


def load_seeds(
    path: str | Path,
    *,
    strict: bool = False,
) -> list[dict[str, Any]]:
    """Load seed records from a JSONL file."""
    resolved = resolve_path(path)
    if not resolved.is_file():
        tried = [str(path), str(resolved)]
        raise FileNotFoundError(f"Seed file not found. Tried: {tried}")

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


def normalize_seed_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign canonical opaque seed IDs."""
    normalized = [dict(row) for row in rows]
    counter = 1
    for row in normalized:
        row["seed_id"] = f"seed_{counter:06d}"
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

PROMPTS_DIR = BASE_DIR / "prompts"


def load_prompt_text(filename: str) -> str:
    """Load a prompt file from the prompts/ directory."""
    path = PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def normalize_seed_context(value: str | None) -> str | None:
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

TRANSCRIPTS_FILE = "transcripts.jsonl"
SCORES_FILE = "scores.jsonl"

# ── Design helpers ────────────────────────────────────────────


def design_factors(design: dict[str, Any]) -> tuple[str, ...]:
    """Return user-defined design factors in stable order.

    Excludes metadata keys and the reserved ``failure_mode`` factor.
    """
    return tuple(
        key for key in design if not key.startswith("_") and key != "failure_mode"
    )

DESIGN_FILE = "design.json"


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


def load_taxonomy(path: str | Path) -> dict[str, Any]:
    """Load and normalize a taxonomy JSON file."""
    taxonomy = json.loads(resolve_path(path).read_text(encoding="utf-8"))
    for failure_mode in taxonomy.get("failure_modes", []):
        permissible = get_permissible_flag(failure_mode)
        if permissible is not None:
            failure_mode["permissible"] = permissible
    return taxonomy


def permissible_by_failure_mode(taxonomy: dict[str, Any] | None) -> dict[str, bool]:
    """Return canonical permissibility flags keyed by failure_mode name."""
    failure_modes = (taxonomy or {}).get("failure_modes")
    if not isinstance(failure_modes, list):
        return {}
    return {
        str(entry.get("name") or ""): bool(entry.get("permissible"))
        for entry in failure_modes
        if isinstance(entry, dict) and str(entry.get("name") or "")
    }


def definitions_by_failure_mode(taxonomy: dict[str, Any] | None) -> dict[str, str]:
    """Return canonical failure_mode definitions keyed by failure_mode name."""
    failure_modes = (taxonomy or {}).get("failure_modes")
    if not isinstance(failure_modes, list):
        return {}
    return {
        str(entry.get("name") or ""): str(entry.get("definition") or "")
        for entry in failure_modes
        if isinstance(entry, dict) and str(entry.get("name") or "")
    }


def taxonomy_definition(
    taxonomy_definition_by_name: dict[str, str],
    failure_mode_name: str,
) -> str:
    """Return a failure_mode's taxonomy definition or raise on missing taxonomy."""
    try:
        return taxonomy_definition_by_name[failure_mode_name]
    except KeyError as exc:
        raise ValueError(
            f"failure_mode '{failure_mode_name}' is missing from taxonomy.failure_modes"
        ) from exc


def taxonomy_permissible(
    taxonomy_permissible_by_name: dict[str, bool],
    failure_mode_name: str,
) -> bool:
    """Return a failure_mode's taxonomy permissibility or raise on missing taxonomy."""
    try:
        return taxonomy_permissible_by_name[failure_mode_name]
    except KeyError as exc:
        raise ValueError(
            f"failure_mode '{failure_mode_name}' is missing from taxonomy.failure_modes"
        ) from exc


def row_failure_mode(row: dict[str, Any]) -> str:
    """Return failure_mode name from a row's factors, or empty string if absent.

    Seed/transcript/score rows carry failure_mode inside `factors`; this is the
    single, canonical accessor used everywhere downstream.
    """
    factors = row.get("factors")
    if not isinstance(factors, dict):
        return ""
    value = factors.get("failure_mode")
    return str(value) if value else ""


def row_factors(row: dict[str, Any]) -> dict[str, str] | None:
    """Return the row's `factors` dict if present and well-formed, else None."""
    factors = row.get("factors")
    return factors if isinstance(factors, dict) else None


def _iter_nonempty_lines(path: Path) -> Iterable[tuple[int, str]]:
    with open(path, encoding="utf-8") as handle:
        for lineno, raw_line in enumerate(handle, 1):
            line = raw_line.strip()
            if line:
                yield lineno, line
