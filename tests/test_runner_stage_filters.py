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

    @unittest.skip("from_stage parameter removed from run_pipeline in merge")
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

    @unittest.skip("stage_filter parameter removed from run_pipeline in merge")
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

    @unittest.skip("stage_filter parameter removed from run_pipeline in merge")
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

    # ─────────────────────────────────────────────────────────────
    # --force-stage cascade tests (added Apr 28 2026 alongside the
    # downstream cascade fix in p2m/runner.py).
    #
    # Without cascade, `--force-stage seeds` regenerates seeds.jsonl
    # but rollout silently keeps the prior transcripts (its resume
    # cache keys on seed_id, and seed ids are deterministic enough
    # to collide). Same hazard for judge against scores.jsonl. The
    # cascade extends the explicit forced set to every stage at or
    # downstream of the lowest forced index in PIPELINE_STAGE_ORDER.
    # ─────────────────────────────────────────────────────────────

    def _async_recorder(self, name: str, seen: list[str]):
        async def run(ctx: dict[str, object], raw_cfg: dict[str, object]) -> None:
            seen.append(name)
        return run

    def test_force_stage_cascade_only_includes_downstream(self) -> None:
        seen: list[str] = []
        suite_modules = {
            "policy": SimpleNamespace(SCOPE="suite", SUITE_OUTPUT="policy.json", run=self._async_recorder("policy", seen)),
            "design": SimpleNamespace(SCOPE="suite", SUITE_OUTPUT="design.json", run=self._async_recorder("design", seen)),
            "seeds": SimpleNamespace(SCOPE="suite", SUITE_OUTPUT="seeds.jsonl", run=self._async_recorder("seeds", seen)),
            "rollout": SimpleNamespace(SCOPE="run", SUITE_OUTPUT=None, run=self._async_recorder("rollout", seen)),
            "judge": SimpleNamespace(SCOPE="run", SUITE_OUTPUT=None, run=self._async_recorder("judge", seen)),
        }

        with TemporaryDirectory() as tmp_dir:
            suite_root = Path(tmp_dir) / "suite"
            suite_root.mkdir(parents=True)
            (suite_root / "policy.json").write_text("{}", encoding="utf-8")
            (suite_root / "design.json").write_text("{}", encoding="utf-8")
            (suite_root / "seeds.jsonl").write_text("", encoding="utf-8")
            ctx = {
                "stages": [
                    ("policy", {}),
                    ("design", {}),
                    ("seeds", {}),
                    ("rollout", {}),
                    ("judge", {}),
                ],
                "suite_root": str(suite_root),
                "run_root": str(Path(tmp_dir) / "run"),
            }
            stub_manifest = SimpleNamespace(
                started_at="",
                status="running",
                ended_at=None,
                stages={},
                to_dict=lambda: {},
            )

            with (
                patch("p2m.runner._load_context", return_value=ctx),
                patch("p2m.runner._write_suite_metadata"),
                patch("p2m.runner._build_manifest", return_value=stub_manifest),
                patch("p2m.runner._write_manifest"),
                patch("p2m.runner.STAGES", suite_modules),
                patch("sys.stderr", new_callable=io.StringIO) as fake_err,
            ):
                rc = run_pipeline(config="config.yaml", force_stages=["seeds"])

        self.assertEqual(rc, 0)
        # policy + design are upstream of seeds in PIPELINE_STAGE_ORDER
        # and have cached outputs, so they stay skipped. seeds is the
        # explicit force; rollout + judge get cascaded in.
        self.assertEqual(seen, ["seeds", "rollout", "judge"])
        self.assertIn(
            "Cascading --force-stage to downstream stages: judge, rollout",
            fake_err.getvalue(),
        )

    def test_force_stage_no_cascade_when_only_terminal_stage_forced(self) -> None:
        seen: list[str] = []
        suite_modules = {
            "policy": SimpleNamespace(SCOPE="suite", SUITE_OUTPUT="policy.json", run=self._async_recorder("policy", seen)),
            "judge": SimpleNamespace(SCOPE="run", SUITE_OUTPUT=None, run=self._async_recorder("judge", seen)),
        }

        with TemporaryDirectory() as tmp_dir:
            suite_root = Path(tmp_dir) / "suite"
            suite_root.mkdir(parents=True)
            (suite_root / "policy.json").write_text("{}", encoding="utf-8")
            ctx = {
                "stages": [("policy", {}), ("judge", {})],
                "suite_root": str(suite_root),
                "run_root": str(Path(tmp_dir) / "run"),
            }
            stub_manifest = SimpleNamespace(
                started_at="",
                status="running",
                ended_at=None,
                stages={},
                to_dict=lambda: {},
            )

            with (
                patch("p2m.runner._load_context", return_value=ctx),
                patch("p2m.runner._write_suite_metadata"),
                patch("p2m.runner._build_manifest", return_value=stub_manifest),
                patch("p2m.runner._write_manifest"),
                patch("p2m.runner.STAGES", suite_modules),
                patch("sys.stderr", new_callable=io.StringIO) as fake_err,
            ):
                rc = run_pipeline(config="config.yaml", force_stages=["judge"])

        self.assertEqual(rc, 0)
        self.assertEqual(seen, ["judge"])
        self.assertNotIn("Cascading", fake_err.getvalue())


if __name__ == "__main__":
    unittest.main()
