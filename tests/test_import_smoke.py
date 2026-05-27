# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import importlib
import unittest


class ImportSmokeTest(unittest.TestCase):
    def test_cli_imports(self) -> None:
        importlib.import_module("p2m.cli")

    def test_runner_imports(self) -> None:
        importlib.import_module("p2m.runner")

    def test_stratification_stage_imports(self) -> None:
        importlib.import_module("p2m.stages.stratification")

    def test_test_case_labeling_imports(self) -> None:
        importlib.import_module("p2m.analysis.test_case_labeling")
