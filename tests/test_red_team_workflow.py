# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml


class RedTeamWorkflowTest(unittest.TestCase):
    def test_workflow_is_manual_least_privilege_and_sha_pinned(self) -> None:
        path = Path(__file__).resolve().parent.parent / ".github/workflows/red-team.yml"
        text = path.read_text(encoding="utf-8")
        workflow = yaml.safe_load(text)

        self.assertIn("workflow_dispatch", workflow[True])
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertIn(".[redteam]", text)
        uses = re.findall(r"uses:\s*([^\s#]+)", text)
        self.assertTrue(uses)
        for action in uses:
            with self.subTest(action=action):
                self.assertRegex(action, r"@[0-9a-f]{40}$")

    def test_dependency_changes_trigger_regression_unit_tests(self) -> None:
        path = (
            Path(__file__).resolve().parent.parent
            / ".github/workflows/regression.yml"
        )
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        paths = workflow[True]["pull_request"]["paths"]
        self.assertIn("pyproject.toml", paths)
        self.assertIn("uv.lock", paths)


if __name__ == "__main__":
    unittest.main()
