import importlib
import unittest


class ImportSmokeTest(unittest.TestCase):
    def test_cli_imports(self) -> None:
        importlib.import_module("assert_eval.cli")

    def test_runner_imports(self) -> None:
        importlib.import_module("assert_eval.runner")

    def test_stratification_stage_imports(self) -> None:
        importlib.import_module("assert_eval.stages.stratification")

    def test_test_case_labeling_imports(self) -> None:
        importlib.import_module("assert_eval.analysis.test_case_labeling")
