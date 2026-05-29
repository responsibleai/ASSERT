# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Regression test for issue #103.

The suite page's "Export evaluation set" button links to
``/api/download/{suite}/<file>``. The download endpoint enforces a basename
allowlist before serving any file. If the button's URL drifts from the
allowlist (as happened with the legacy ``seeds.jsonl`` name) the download
silently 403s.

This test pins both sides of the contract so the failure mode cannot recur.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUITE_PAGE = ROOT / "viewer" / "src" / "routes" / "suite" / "[suite_id]" / "+page.svelte"
DOWNLOAD_HANDLER = ROOT / "viewer" / "src" / "routes" / "api" / "download" / "[...path]" / "+server.ts"

EXPORT_URL_PATTERN = re.compile(r"/api/download/\{[^}]+\}/([\w.\-]+\.jsonl)")
ALLOWED_FILES_PATTERN = re.compile(
    r"ALLOWED_FILES\s*=\s*new\s+Set\s*\(\s*\[(?P<body>[^\]]+)\]\s*\)",
    re.DOTALL,
)
STRING_PATTERN = re.compile(r"['\"]([^'\"]+)['\"]")


def _parse_allowlist(handler_source: str) -> set[str]:
    match = ALLOWED_FILES_PATTERN.search(handler_source)
    if match is None:
        raise AssertionError(
            f"Could not locate ALLOWED_FILES set in {DOWNLOAD_HANDLER.relative_to(ROOT)}"
        )
    return {entry for entry in STRING_PATTERN.findall(match.group("body"))}


class ExportEvaluationSetDownloadTest(unittest.TestCase):
    def test_export_button_url_points_at_allowlisted_artifact(self) -> None:
        page_source = SUITE_PAGE.read_text(encoding="utf-8")
        allowlist = _parse_allowlist(DOWNLOAD_HANDLER.read_text(encoding="utf-8"))

        export_filenames = EXPORT_URL_PATTERN.findall(page_source)
        self.assertTrue(
            export_filenames,
            f"No /api/download/<suite>/<file>.jsonl URL found in {SUITE_PAGE.relative_to(ROOT)}",
        )
        for filename in export_filenames:
            with self.subTest(filename=filename):
                self.assertIn(
                    filename,
                    allowlist,
                    (
                        f"Export anchor in {SUITE_PAGE.relative_to(ROOT)} requests "
                        f"'{filename}', but the catch-all download handler's "
                        f"ALLOWED_FILES allowlist does not include it. "
                        f"This is the bug from issue #103 — the download "
                        f"endpoint will return 403 and the file will never "
                        f"reach the user."
                    ),
                )

    def test_canonical_test_set_artifact_is_downloadable(self) -> None:
        allowlist = _parse_allowlist(DOWNLOAD_HANDLER.read_text(encoding="utf-8"))
        self.assertIn(
            "test_set.jsonl",
            allowlist,
            "test_set.jsonl is the canonical artifact for the evaluation set; "
            "removing it from the allowlist would break the export button.",
        )

    def test_legacy_seeds_name_is_not_downloadable(self) -> None:
        allowlist = _parse_allowlist(DOWNLOAD_HANDLER.read_text(encoding="utf-8"))
        self.assertNotIn(
            "seeds.jsonl",
            allowlist,
            (
                "'seeds.jsonl' is legacy vocabulary — the artifact was renamed "
                "to test_set.jsonl by scripts/migrate_artifacts_to_pr23_vocab.py. "
                "Re-introducing the legacy name in the download allowlist would "
                "reintroduce the issue #103 confusion."
            ),
        )


if __name__ == "__main__":
    unittest.main()
