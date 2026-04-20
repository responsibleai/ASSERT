import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from p2m.runner import run_pipeline


class RunnerStageFilterTest(unittest.TestCase):
    def _module(self, name: str, seen: list[str]) -> SimpleNamespace:
        async def run(ctx: dict[str, object], raw_cfg: dict[str, object]) -> None:
            seen.append(name)

        return SimpleNamespace(SCOPE="suite", SUITE_OUTPUT=None, run=run)

    def test_from_stage_uses_configured_order(self) -> None:
        seen: list[str] = []
        with TemporaryDirectory() as tmp_dir:
            suite_root = Path(tmp_dir) / "suite"
            ctx = {
                "stages": [("prepare", {}), ("judge", {}), ("report", {})],
                "suite_root": str(suite_root),
                "run_root": None,
            }
            stages = {name: self._module(name, seen) for name in ["prepare", "judge", "report"]}

            with (
                patch("p2m.runner._load_context", return_value=ctx),
                patch("p2m.runner._write_suite_metadata"),
                patch("p2m.runner.STAGES", stages),
            ):
                rc = run_pipeline(config="config.yaml", from_stage="judge")

        self.assertEqual(rc, 0)
        self.assertEqual(seen, ["judge", "report"])

    def test_stage_filter_runs_only_selected_stages(self) -> None:
        seen: list[str] = []
        with TemporaryDirectory() as tmp_dir:
            suite_root = Path(tmp_dir) / "suite"
            ctx = {
                "stages": [("prepare", {}), ("judge", {}), ("report", {})],
                "suite_root": str(suite_root),
                "run_root": None,
            }
            stages = {name: self._module(name, seen) for name in ["prepare", "judge", "report"]}

            with (
                patch("p2m.runner._load_context", return_value=ctx),
                patch("p2m.runner._write_suite_metadata"),
                patch("p2m.runner.STAGES", stages),
            ):
                rc = run_pipeline(config="config.yaml", stage_filter=["report", "prepare"])

        self.assertEqual(rc, 0)
        self.assertEqual(seen, ["prepare", "report"])

    def test_stage_filter_rejects_stages_missing_from_config(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            suite_root = Path(tmp_dir) / "suite"
            ctx = {
                "stages": [("judge", {})],
                "suite_root": str(suite_root),
                "run_root": None,
            }
            stages = {"judge": self._module("judge", [])}

            with (
                patch("p2m.runner._load_context", return_value=ctx),
                patch("p2m.runner._write_suite_metadata"),
                patch("p2m.runner.STAGES", stages),
                patch("sys.stderr", new_callable=io.StringIO) as fake_err,
            ):
                rc = run_pipeline(config="config.yaml", stage_filter=["report"])

        self.assertEqual(rc, 1)
        self.assertIn("--stage stage(s) not present in config: report", fake_err.getvalue())


if __name__ == "__main__":
    unittest.main()
