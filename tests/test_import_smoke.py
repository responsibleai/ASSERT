# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import importlib
from importlib.metadata import metadata
import unittest


class ImportSmokeTest(unittest.TestCase):
    def test_cli_imports(self) -> None:
        importlib.import_module("assert_ai.cli")

    def test_runner_imports(self) -> None:
        importlib.import_module("assert_ai.runner")

    def test_stratification_stage_imports(self) -> None:
        importlib.import_module("assert_ai.stages.stratification")

    def test_test_case_labeling_imports(self) -> None:
        importlib.import_module("assert_ai.analysis.test_case_labeling")

    def test_public_extras_match_release_contract(self) -> None:
        package_metadata = metadata("assert-ai")
        self.assertEqual(
            set(package_metadata.get_all("Provides-Extra") or []),
            {"acs", "all", "analysis", "azure-auth", "dev", "embeddings", "phoenix"},
        )

        requirements = package_metadata.get_all("Requires-Dist") or []
        expected_dependencies = {
            "acs": {"acs-generator", "agent-control-specification"},
            "analysis": {"numpy", "openai"},
            "azure-auth": {"azure-identity"},
            "embeddings": {"numpy", "sentence-transformers"},
            "phoenix": {"arize-phoenix", "arize-phoenix-otel", "pandas"},
        }
        for extra, dependencies in expected_dependencies.items():
            extra_requirements = {
                requirement.split(";", 1)[0].split(" ", 1)[0].split("<", 1)[0].split(">", 1)[0]
                for requirement in requirements
                if f'extra == "{extra}"' in requirement
            }
            self.assertEqual(extra_requirements, dependencies, extra)

        all_requirement = next(
            requirement for requirement in requirements if 'extra == "all"' in requirement
        )
        for extra in expected_dependencies:
            self.assertIn(extra, all_requirement)
