# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Migrate local run artifacts to the PR #23 vocabulary."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RENAMES = {
    "policy.json": "systematization.json",
    "seeds.jsonl": "test_set.jsonl",
    "design.json": "stratification.json",
    "transcripts.jsonl": "inference_set.jsonl",
}
JSONL_NAMES = {"test_set.jsonl", "inference_set.jsonl"}
FIELD_RENAMES = {"kind": "type", "seed_id": "test_case_id"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="artifacts", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def rewrite_row(row: dict) -> bool:
    changed = False
    for old, new in FIELD_RENAMES.items():
        if old not in row:
            continue
        if new not in row:
            row[new] = row[old]
        del row[old]
        changed = True
    return changed


def rewrite_jsonl(label: Path, source: Path, dry_run: bool) -> tuple[int, bool]:
    lines: list[str] = []
    updated = 0
    with source.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            newline = "\n" if line.endswith("\n") else ""
            body = line[:-1] if newline else line
            if not body.strip():
                lines.append(line)
                continue
            try:
                row = json.loads(body)
            except json.JSONDecodeError:
                print(f"SKIP JSONL rewrite (invalid JSON): {source}:{line_no}")
                return 0, True
            if not isinstance(row, dict):
                print(f"SKIP JSONL rewrite (top-level value is not object): {source}:{line_no}")
                return 0, True
            if rewrite_row(row):
                updated += 1
            lines.append(json.dumps(row, ensure_ascii=False) + newline)

    if updated == 0:
        return 0, False
    if dry_run:
        print(f"WOULD rewrite {label}: {updated} records updated")
    else:
        tmp = label.with_name(label.name + ".tmp-pr23")
        with tmp.open("w", encoding="utf-8", newline="") as handle:
            handle.writelines(lines)
        tmp.replace(label)
        print(f"REWRITE {label}: {updated} records updated")
    return updated, False


def main() -> int:
    args = parse_args()
    root = args.root
    if not root.exists():
        print(f"ERROR: root does not exist: {root}")
        return 1

    renamed = conflicts_deleted = records_updated = files_rewritten = 0
    skipped_shape_dirs: set[Path] = set()
    rewrite_sources: dict[Path, Path] = {}

    try:
        old_files = sorted(p for p in root.rglob("*") if p.is_file() and p.name in RENAMES)
        for old in old_files:
            new = old.with_name(RENAMES[old.name])
            if new.exists():
                print(f"SKIP rename (NEW exists): {old}")
                if args.dry_run:
                    print(f"WOULD delete OLD due to conflict: {old}")
                else:
                    old.unlink()
                    print(f"DELETE OLD due to conflict: {old}")
                conflicts_deleted += 1
                source = new
            else:
                if args.dry_run:
                    print(f"WOULD RENAME: {old} -> {new}")
                    source = old
                else:
                    old.rename(new)
                    print(f"RENAME: {old} -> {new}")
                    source = new
                renamed += 1
            if new.name in JSONL_NAMES:
                rewrite_sources[new] = source

        current_jsonl = sorted(p for p in root.rglob("*") if p.is_file() and p.name in JSONL_NAMES)
        for path in current_jsonl:
            rewrite_sources.setdefault(path, path)

        for label, source in sorted(rewrite_sources.items()):
            if args.verbose:
                print(f"CHECK JSONL fields: {label}")
            updated, skipped = rewrite_jsonl(label, source, args.dry_run)
            records_updated += updated
            files_rewritten += int(updated > 0 and not args.dry_run)
            if skipped:
                skipped_shape_dirs.add(label.parent)
    except OSError as exc:
        print(f"ERROR: {exc}")
        return 1

    print("SUMMARY:")
    print(f"  renamed: {renamed}")
    print(f"  conflicts-deleted: {conflicts_deleted}")
    print(f"  jsonl files rewritten: {files_rewritten}")
    print(f"  records updated: {records_updated}")
    print(f"  skipped unexpected-shape dirs: {len(skipped_shape_dirs)}")
    for directory in sorted(skipped_shape_dirs):
        print(f"    {directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
