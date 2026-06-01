# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Guard against re-introduction of the legacy ``p2m`` name.

The Python package was renamed from ``p2m`` to ``assert_ai`` and the CLI from
``p2m`` to ``assert-ai``. This test walks every tracked source file in the
repo and fails if the legacy name appears outside the small allowlist of
historical artifact snapshots and this test file itself.

Why a guard test:
- ``p2m`` is short, easy to mistype, and easy to copy-paste back in from old
  notes, AI suggestions, or merged branches. A scan-based test catches that
  before it leaks into a release.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SELF_REL_PATH = Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()

# Match "p2m" as a whole word, case-insensitive. The word boundaries make
# substrings inside larger identifiers (e.g., "snap2map") safe.
_P2M_PATTERN = re.compile(r"\bp2m\b", re.IGNORECASE)

# Path prefixes (forward-slash, relative to REPO_ROOT) where the legacy name is
# intentionally preserved.
#
# Historical artifact snapshots under examples/**/artifacts/results/** are
# frozen records of what was run at the time. Rewriting them would falsify the
# historical record, so they remain untouched.
_ALLOWED_PATH_PREFIXES: tuple[str, ...] = (
    "examples/incident_triage_agent/artifacts/results/",
)

# File extensions that are binary or otherwise not useful to scan as text.
_SKIP_EXTS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz",
    ".mp3", ".mp4", ".mov", ".webm", ".wav",
    ".pyc", ".pyo",
})


def _tracked_files() -> list[str]:
    """Return all paths tracked by git, as forward-slash relative paths."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _is_allowed(rel_path: str) -> bool:
    rel_path = rel_path.replace("\\", "/")
    if rel_path == SELF_REL_PATH:
        return True
    return any(rel_path.startswith(prefix) for prefix in _ALLOWED_PATH_PREFIXES)


class NoLegacyP2MReferencesTest(unittest.TestCase):
    """The legacy ``p2m`` name must not exist anywhere in tracked sources."""

    def test_no_p2m_in_tracked_files(self) -> None:
        violations: list[str] = []
        for rel in _tracked_files():
            if _is_allowed(rel):
                continue
            ext = Path(rel).suffix.lower()
            if ext in _SKIP_EXTS:
                continue
            abs_path = REPO_ROOT / rel
            if not abs_path.is_file():
                continue
            try:
                text = abs_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if _P2M_PATTERN.search(line):
                    violations.append(f"{rel}:{lineno}: {line.strip()}")

        if violations:
            joined = "\n  ".join(violations)
            self.fail(
                "The legacy 'p2m' name leaked back into the codebase. Use "
                "'assert_ai' (Python import) or 'assert-ai' (CLI) instead. "
                "If a reference is intentional historical context, add its "
                "path to _ALLOWED_PATH_PREFIXES in "
                "tests/test_no_p2m_references.py.\n\n"
                f"Violations ({len(violations)}):\n  {joined}"
            )


if __name__ == "__main__":
    unittest.main()
