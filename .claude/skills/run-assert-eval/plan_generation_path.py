#!/usr/bin/env python3
"""Find prior ASSERT generation paths and propose an isolated output directory.

This helper inspects directory entries and YAML filenames only. It never opens,
parses, hashes, or otherwise reads a generated YAML file.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any


SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
YAML_SUFFIXES = {".yaml", ".yml"}


class GenerationPathError(ValueError):
    """Raised when an isolated generation path cannot be planned safely."""


def _validate_slug(name: str) -> str:
    if not SLUG.fullmatch(name):
        raise GenerationPathError(
            "name must be a lowercase slug containing only letters, digits, underscores, or hyphens"
        )
    return name


def _validate_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise GenerationPathError("date must use YYYY-MM-DD") from error


def _matches_generation_directory(name: str, candidate: str) -> bool:
    pattern = rf"^{re.escape(name)}(?:_[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}(?:_[1-9][0-9]*)?)?$"
    return re.fullmatch(pattern, candidate) is not None


def _count_yaml_filenames(directory: Path) -> int:
    count = 0
    for current_root, child_directories, filenames in os.walk(directory, followlinks=False):
        root = Path(current_root)
        child_directories[:] = [
            child
            for child in child_directories
            if not (root / child).is_symlink()
        ]
        count += sum(Path(filename).suffix.lower() in YAML_SUFFIXES for filename in filenames)
    return count


def _describe_match(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        return {"path": str(path), "kind": "symlink", "yaml_file_count": None}
    if path.is_dir():
        return {
            "path": str(path),
            "kind": "directory",
            "yaml_file_count": _count_yaml_filenames(path),
        }
    return {"path": str(path), "kind": "file", "yaml_file_count": None}


def plan_generation(
    *,
    eval_type: str,
    name: str,
    root: Path,
    run_date: str,
) -> dict[str, Any]:
    if eval_type not in {"harm", "system"}:
        raise GenerationPathError("eval_type must be harm or system")
    name = _validate_slug(name)
    run_date = _validate_date(run_date)
    root = Path(root)
    if root.is_symlink():
        raise GenerationPathError("generation root must not be a symlink")
    if root.exists() and not root.is_dir():
        raise GenerationPathError("generation root must be a directory")

    matching_paths = []
    if root.is_dir():
        matching_paths = sorted(
            (
                path
                for path in root.iterdir()
                if _matches_generation_directory(name, path.name)
            ),
            key=lambda path: path.name,
        )
    matches = [_describe_match(path) for path in matching_paths]
    prior_generations = [
        match
        for match in matches
        if isinstance(match["yaml_file_count"], int) and match["yaml_file_count"] > 0
    ]
    unknown_matches = [match for match in matches if match["yaml_file_count"] is None]

    if not matches:
        proposed = root / name
        dated = False
    else:
        dated = True
        proposed = root / f"{name}_{run_date}"
        ordinal = 2
        while proposed.exists() or proposed.is_symlink():
            proposed = root / f"{name}_{run_date}_{ordinal}"
            ordinal += 1

    return {
        "eval_type": eval_type,
        "name": name,
        "root": str(root),
        "matching_paths": matches,
        "prior_generation_directories": prior_generations,
        "requires_confirmation": bool(prior_generations or unknown_matches),
        "proposed_directory": str(proposed),
        "uses_date_suffix": dated,
        "inspection_policy": "path-and-filename-metadata-only",
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Detect prior same-name generations by path only and propose a new isolated directory."
        )
    )
    parser.add_argument("--eval-type", required=True, choices=("harm", "system"))
    parser.add_argument("--name", required=True, help="Stable lowercase harm or system slug")
    parser.add_argument("--root", type=Path, default=Path("examples"))
    parser.add_argument("--date", default=date.today().isoformat(), help="Run date (YYYY-MM-DD)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        plan = plan_generation(
            eval_type=args.eval_type,
            name=args.name,
            root=args.root,
            run_date=args.date,
        )
    except (OSError, GenerationPathError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(plan, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())