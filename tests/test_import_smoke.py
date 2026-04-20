import importlib
import unittest


class ImportSmokeTest(unittest.TestCase):
    def test_cli_imports(self) -> None:
        importlib.import_module("p2m.cli")

    def test_runner_imports(self) -> None:
        importlib.import_module("p2m.runner")

    def test_design_stage_imports(self) -> None:
        importlib.import_module("p2m.stages.design")

    def test_seed_labeling_imports(self) -> None:
        importlib.import_module("p2m.analysis.seed_labeling")
