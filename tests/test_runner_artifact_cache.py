import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from p2m.runner import run_pipeline


class RunnerArtifactCacheTest(unittest.TestCase):
    def _ctx(
        self,
        root: Path,
        *,
        behavior: str = "Travel planner must produce grounded itineraries.",
        run_id: str | None = None,
        design_level_count: int = 3,
        prompt_sample_size: int = 1,
        include_inference: bool = False,
        include_upstream: bool = True,
    ) -> dict[str, Any]:
        config_path = root / "config.yaml"
        config_path.write_text("suite: suite-a\n", encoding="utf-8")
        stages: list[tuple[str, dict[str, Any]]] = []
        if include_upstream:
            stages.extend(
                [
                    ("systematize", {"model": {"name": "azure/gpt-5.4"}, "behavior_category_count": 2}),
                    (
                        "test_set",
                        {
                            "stratify": {
                                "model": {"name": "azure/gpt-5.4"},
                                "level_count": design_level_count,
                            },
                            "prompt": {
                                "model": {"name": "azure/gpt-5.4"},
                                "sample_size": prompt_sample_size,
                            },
                        },
                    ),
                ]
            )
        if include_inference:
            stages.append(("inference", {}))
        return {
            "config_path": config_path,
            "artifacts_root": root / "artifacts",
            "results_dir": root / "results",
            "suite_root": root / "results" / "suite-a",
            "run_root": root / "results" / "suite-a" / run_id if run_id else None,
            "suite_id": "suite-a",
            "run_id": run_id,
            "behavior_name": "travel_planner_eval",
            "concept_name": "travel_planner_eval",
            "behavior": behavior,
            "context": "Travel planner with flight and hotel tools.",
            "dimensions": [],
            "target": None,
            "evaluation": None,
            "stages": stages,
        }

    def _modules(self, seen: list[str]) -> dict[str, SimpleNamespace]:
        async def systematize(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
            seen.append("systematize")
            Path(ctx["systematization_path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(ctx["systematization_path"]).write_text("{}", encoding="utf-8")
            Path(ctx["taxonomy_path"]).write_text(
                json.dumps({"behavior": {"name": ctx["behavior_name"]}, "behavior_categories": []}),
                encoding="utf-8",
            )
            return {
                "taxonomy_path": ctx["taxonomy_path"],
                "systematization_path": ctx["systematization_path"],
            }

        async def test_set(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
            seen.append("test_set")
            Path(ctx["design_path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(ctx["design_path"]).write_text("{}", encoding="utf-8")
            Path(ctx["test_set_path"]).write_text(
                '{"type":"prompt","test_case_id":"seed_000001","seed":{"prompt":"hi"}}\n',
                encoding="utf-8",
            )
            return {"design_path": ctx["design_path"], "test_set_path": ctx["test_set_path"]}

        async def inference(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
            seen.append(f"inference:{Path(ctx['test_set_path']).parent.name}")
            run_root = Path(ctx["run_root"])
            run_root.mkdir(parents=True, exist_ok=True)
            transcripts = run_root / "transcripts.jsonl"
            transcripts.write_text('{"type":"prompt","test_case_id":"seed_000001"}\n', encoding="utf-8")
            return {"transcripts_path": str(transcripts)}

        return {
            "systematize": SimpleNamespace(SCOPE="suite", SUITE_OUTPUT="taxonomy.json", run=systematize),
            "test_set": SimpleNamespace(SCOPE="suite", SUITE_OUTPUT="test_set.jsonl", run=test_set),
            "inference": SimpleNamespace(SCOPE="run", SUITE_OUTPUT=None, run=inference),
        }

    def _run_with_contexts(
        self,
        contexts: list[dict[str, Any]],
        seen: list[str],
        *,
        force_stages: list[list[str] | None] | None = None,
    ) -> list[int]:
        modules = self._modules(seen)
        with (
            patch("p2m.runner._load_context", side_effect=contexts),
            patch("p2m.runner.STAGES", modules),
            patch("sys.__stderr__", new_callable=io.StringIO),
        ):
            return [
                run_pipeline(
                    config=str(ctx["config_path"]),
                    force_stages=None if force_stages is None else force_stages[index],
                )
                for index, ctx in enumerate(contexts)
            ]

    def test_identical_inputs_reuse_systematize_and_test_set(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            seen: list[str] = []
            codes = self._run_with_contexts([self._ctx(root), self._ctx(root)], seen)

            self.assertEqual(codes, [0, 0])
            self.assertEqual(seen, ["systematize", "test_set"])
            self.assertTrue((root / "results" / "suite-a" / "artifacts" / "systematize" / "v0001" / "taxonomy.json").exists())
            self.assertFalse((root / "results" / "suite-a" / "artifacts" / "systematize" / "v0002").exists())

    def test_behavior_change_regenerates_all_upstream_artifacts(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            seen: list[str] = []
            codes = self._run_with_contexts(
                [
                    self._ctx(root),
                    self._ctx(root, behavior="Changed travel planner behavior."),
                ],
                seen,
            )

            self.assertEqual(codes, [0, 0])
            self.assertEqual(seen, ["systematize", "test_set", "systematize", "test_set"])
            self.assertTrue((root / "results" / "suite-a" / "artifacts" / "systematize" / "v0002" / "taxonomy.json").exists())
            self.assertTrue((root / "results" / "suite-a" / "artifacts" / "test_set" / "v0002" / "design.json").exists())
            self.assertTrue((root / "results" / "suite-a" / "artifacts" / "test_set" / "v0002" / "test_set.jsonl").exists())

    def test_stratify_config_change_reuses_systematize_but_regenerates_test_set(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            seen: list[str] = []
            codes = self._run_with_contexts(
                [
                    self._ctx(root, design_level_count=3),
                    self._ctx(root, design_level_count=4),
                ],
                seen,
            )

            self.assertEqual(codes, [0, 0])
            self.assertEqual(seen, ["systematize", "test_set", "test_set"])
            self.assertFalse((root / "results" / "suite-a" / "artifacts" / "systematize" / "v0002").exists())
            self.assertTrue((root / "results" / "suite-a" / "artifacts" / "test_set" / "v0002" / "design.json").exists())
            self.assertTrue((root / "results" / "suite-a" / "artifacts" / "test_set" / "v0002" / "test_set.jsonl").exists())

    def test_test_set_config_change_reuses_systematize_but_regenerates_test_set(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            seen: list[str] = []
            codes = self._run_with_contexts(
                [
                    self._ctx(root, prompt_sample_size=1),
                    self._ctx(root, prompt_sample_size=2),
                ],
                seen,
            )

            self.assertEqual(codes, [0, 0])
            self.assertEqual(seen, ["systematize", "test_set", "test_set"])
            self.assertFalse((root / "results" / "suite-a" / "artifacts" / "systematize" / "v0002").exists())
            self.assertTrue((root / "results" / "suite-a" / "artifacts" / "test_set" / "v0002" / "test_set.jsonl").exists())

    def test_force_stage_test_set_creates_new_test_set_version_only(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            seen: list[str] = []
            codes = self._run_with_contexts(
                [self._ctx(root), self._ctx(root)],
                seen,
                force_stages=[None, ["test_set"]],
            )

            self.assertEqual(codes, [0, 0])
            self.assertEqual(seen, ["systematize", "test_set", "test_set"])
            self.assertFalse((root / "results" / "suite-a" / "artifacts" / "systematize" / "v0002").exists())
            self.assertTrue((root / "results" / "suite-a" / "artifacts" / "test_set" / "v0002" / "test_set.jsonl").exists())

    def test_force_stage_systematize_cascades_through_test_set(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            seen: list[str] = []
            codes = self._run_with_contexts(
                [self._ctx(root), self._ctx(root)],
                seen,
                force_stages=[None, ["systematize"]],
            )

            self.assertEqual(codes, [0, 0])
            self.assertEqual(seen, ["systematize", "test_set", "systematize", "test_set"])
            for stage in ("systematize", "test_set"):
                self.assertTrue(
                    (root / "results" / "suite-a" / "artifacts" / stage / "v0002").exists(),
                    msg=f"expected v0002 of {stage}",
                )

    def test_reuse_path_restores_legacy_compatibility_files(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            seen: list[str] = []
            codes_a = self._run_with_contexts([self._ctx(root)], seen)
            self.assertEqual(codes_a, [0])

            suite_root = root / "results" / "suite-a"
            for filename in ("taxonomy.json", "systematization.json", "design.json", "test_set.jsonl"):
                (suite_root / filename).unlink(missing_ok=True)

            codes_b = self._run_with_contexts([self._ctx(root)], seen)
            self.assertEqual(codes_b, [0])
            for filename in ("taxonomy.json", "systematization.json", "design.json", "test_set.jsonl"):
                self.assertTrue(
                    (suite_root / filename).exists(),
                    msg=f"reuse path failed to restore compatibility {filename}",
                )

    def test_inference_records_test_set_artifact_version_and_runs_per_run(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            seen: list[str] = []
            codes = self._run_with_contexts(
                [
                    self._ctx(root, run_id="run-a", include_inference=True),
                    self._ctx(root, run_id="run-b", include_inference=True),
                ],
                seen,
            )

            self.assertEqual(codes, [0, 0])
            self.assertEqual(seen, ["systematize", "test_set", "inference:v0001", "inference:v0001"])
            manifest = json.loads(
                (root / "results" / "suite-a" / "run-b" / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["artifact_versions"]["test_set"]["version"], "v0001")
            self.assertEqual(
                Path(manifest["artifact_versions"]["test_set"]["path"]),
                Path("artifacts") / "test_set" / "v0001" / "test_set.jsonl",
            )
            self.assertNotIn("relative_path", manifest["artifact_versions"]["test_set"])
            self.assertNotIn("relative_metadata_path", manifest["artifact_versions"]["test_set"])

    def test_inference_only_config_uses_latest_test_set_artifact_version(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            seen: list[str] = []
            codes = self._run_with_contexts(
                [
                    self._ctx(root, run_id="run-a", include_inference=True),
                    self._ctx(root, run_id="run-b", include_inference=True, include_upstream=False),
                ],
                seen,
            )

            self.assertEqual(codes, [0, 0])
            self.assertEqual(seen, ["systematize", "test_set", "inference:v0001", "inference:v0001"])
            manifest = json.loads(
                (root / "results" / "suite-a" / "run-b" / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["artifact_versions"]["test_set"]["version"], "v0001")

    def test_user_save_dir_in_raw_cfg_does_not_redirect_cached_outputs(self) -> None:
        """Regression for Copilot review #003.

        When a user supplies ``save_dir`` (or ``save_path``) in the raw stage
        config for a cacheable stage, the runner must override it so the
        artifact still lands in the versioned cache directory. Otherwise the
        stage writes outside the artifact dir, ``finalize_artifact_plan`` cannot
        find the outputs to hash, and the pipeline fails.
        """

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            seen: list[str] = []

            # Realistic stages that honor raw_cfg["save_dir"] / "save_path".
            # The test verifies the runner's cache override takes precedence.
            async def systematize(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
                seen.append("systematize")
                save_dir = Path(raw_cfg.get("save_dir") or ctx["systematize_artifact_dir"])
                save_dir.mkdir(parents=True, exist_ok=True)
                (save_dir / "taxonomy.json").write_text(
                    json.dumps({"behavior": {"name": ctx["behavior_name"]}, "behavior_categories": []}),
                    encoding="utf-8",
                )
                (save_dir / "systematization.json").write_text("{}", encoding="utf-8")
                return {
                    "taxonomy_path": str(save_dir / "taxonomy.json"),
                    "systematization_path": str(save_dir / "systematization.json"),
                }

            async def test_set(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
                seen.append("test_set")
                save_path = Path(raw_cfg.get("save_path") or ctx["test_set_path"])
                save_path.parent.mkdir(parents=True, exist_ok=True)
                design_path = save_path.parent / "design.json"
                design_path.write_text("{}", encoding="utf-8")
                save_path.write_text(
                    '{"type":"prompt","test_case_id":"seed_000001","seed":{"prompt":"hi"}}\n',
                    encoding="utf-8",
                )
                return {"design_path": str(design_path), "test_set_path": str(save_path)}

            modules = {
                "systematize": SimpleNamespace(SCOPE="suite", SUITE_OUTPUT="taxonomy.json", run=systematize),
                "test_set": SimpleNamespace(SCOPE="suite", SUITE_OUTPUT="test_set.jsonl", run=test_set),
            }

            ctx = self._ctx(root)
            # Inject user-provided output overrides into the cacheable stages.
            user_systematize_dir = root / "user-systematize"
            user_test_set_path = root / "user-test_set" / "out.jsonl"
            ctx["stages"] = [
                (
                    "systematize",
                    {
                        "model": {"name": "azure/gpt-5.4"},
                        "behavior_category_count": 2,
                        "save_dir": str(user_systematize_dir),
                    },
                ),
                (
                    "test_set",
                    {
                        "stratify": {"model": {"name": "azure/gpt-5.4"}, "level_count": 3},
                        "prompt": {"model": {"name": "azure/gpt-5.4"}, "sample_size": 1},
                        "save_path": str(user_test_set_path),
                    },
                ),
            ]

            with (
                patch("p2m.runner._load_context", side_effect=[ctx]),
                patch("p2m.runner.STAGES", modules),
                patch("sys.__stderr__", new_callable=io.StringIO),
            ):
                code = run_pipeline(config=str(ctx["config_path"]))

            self.assertEqual(code, 0)
            suite_artifacts = root / "results" / "suite-a" / "artifacts"
            self.assertTrue((suite_artifacts / "systematize" / "v0001" / "taxonomy.json").exists())
            self.assertTrue((suite_artifacts / "systematize" / "v0001" / "systematization.json").exists())
            self.assertTrue((suite_artifacts / "test_set" / "v0001" / "design.json").exists())
            self.assertTrue((suite_artifacts / "test_set" / "v0001" / "test_set.jsonl").exists())
            # The user-supplied directories must be ignored entirely.
            self.assertFalse(user_systematize_dir.exists())
            self.assertFalse(user_test_set_path.exists())

    def test_failed_cacheable_stage_cleans_up_partial_version_dir(self) -> None:
        """Regression for Jake's review (round 5).

        When a cacheable suite stage fails after ``prepare_artifact_plan``
        allocates ``vNNNN/`` (and the stage may have written partial outputs)
        but before ``finalize_artifact_plan`` writes the sidecar, the runner
        must remove the abandoned version directory so it does not leak on
        disk and ``_allocate_version_dir`` does not increment past empty
        slots.
        """

        modules = self._modules([])

        async def failing_test_set(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
            # Simulate the realistic case where a stage writes partial
            # outputs into its allocated artifact_dir before crashing.
            Path(ctx["test_set_path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(ctx["design_path"]).write_text('{"partial": true}', encoding="utf-8")
            Path(ctx["test_set_path"]).write_text(
                '{"type":"prompt","test_case_id":"seed_000001","seed":{"prompt":"hi"}}\n',
                encoding="utf-8",
            )
            raise RuntimeError("simulated test_set failure")

        modules["test_set"] = SimpleNamespace(
            SCOPE="suite", SUITE_OUTPUT="test_set.jsonl", run=failing_test_set
        )

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            with (
                patch("p2m.runner._load_context", return_value=ctx),
                patch("p2m.runner.STAGES", modules),
                patch("sys.__stderr__", new_callable=io.StringIO),
            ):
                code = run_pipeline(config=str(ctx["config_path"]))

            self.assertEqual(code, 1)
            test_set_root = root / "results" / "suite-a" / "artifacts" / "test_set"
            # The abandoned v0001 directory must have been removed.
            self.assertFalse((test_set_root / "v0001").exists())

            # And on a follow-up run, _allocate_version_dir should reuse
            # v0001 rather than incrementing past the cleaned-up slot.
            seen: list[str] = []
            modules2 = self._modules(seen)
            with (
                patch("p2m.runner._load_context", return_value=self._ctx(root)),
                patch("p2m.runner.STAGES", modules2),
                patch("sys.__stderr__", new_callable=io.StringIO),
            ):
                code = run_pipeline(config=str(ctx["config_path"]))

            self.assertEqual(code, 0)
            self.assertTrue((test_set_root / "v0001" / "test_set.jsonl").exists())
            self.assertFalse((test_set_root / "v0002").exists())

    def test_partial_test_set_skips_artifact_finalization(self) -> None:
        """Regression for Jake's review on the absorb of PR #44.

        ``_generate_records`` now tolerates per-batch failures and
        returns a partial ``test_set.jsonl`` plus ``errored_count > 0``.
        Before this fix, the runner finalized the cacheable artifact
        anyway: it wrote ``artifact.json`` next to the partial
        test_set.jsonl, updated ``latest.json``, and a future run with the
        same input hash would silently reuse the smaller-than-requested
        file. This test pins the gate at runner.py: when ``_summary``
        carries a non-zero ``errored_count``, ``finalize_artifact_plan``
        must be skipped so the next run regenerates from scratch.
        """

        async def partial_test_set(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
            Path(ctx["test_set_path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(ctx["design_path"]).write_text("{}", encoding="utf-8")
            Path(ctx["test_set_path"]).write_text(
                '{"type":"prompt","test_case_id":"seed_000001","seed":{"prompt":"hi"}}\n',
                encoding="utf-8",
            )
            return {
                "design_path": ctx["design_path"],
                "test_set_path": ctx["test_set_path"],
                "_summary": {
                    "total": 1,
                    "prompts": 1,
                    "scenarios": 0,
                    "errored_count": 1,
                },
            }

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            seen: list[str] = []

            modules_run1 = self._modules(seen)
            modules_run1["test_set"] = SimpleNamespace(
                SCOPE="suite", SUITE_OUTPUT="test_set.jsonl", run=partial_test_set
            )
            with (
                patch("p2m.runner._load_context", return_value=self._ctx(root)),
                patch("p2m.runner.STAGES", modules_run1),
                patch("sys.__stderr__", new_callable=io.StringIO),
            ):
                code = run_pipeline(config=str(self._ctx(root)["config_path"]))

            # Stage exited cleanly; pipeline considers the run successful
            # because partial-but-some-output is the resilience contract.
            self.assertEqual(code, 0)
            test_set_root = root / "results" / "suite-a" / "artifacts" / "test_set"

            # The partial test_set.jsonl is preserved in the version dir for
            # inspection -- we don't throw it away just because some
            # batches failed.
            self.assertTrue((test_set_root / "v0001" / "test_set.jsonl").exists())

            # But the artifact.json sidecar must NOT exist: without it,
            # _latest_matching_metadata skips this dir on the next run.
            self.assertFalse((test_set_root / "v0001" / "artifact.json").exists())

            # And latest.json must not point at this version (would be
            # caught by activate_latest_artifacts on a follow-up run).
            latest_path = root / "results" / "suite-a" / "latest.json"
            if latest_path.exists():
                latest = json.loads(latest_path.read_text(encoding="utf-8"))
                self.assertNotIn("test_set", latest.get("artifacts", {}))

            # Re-run with identical inputs but a fully-successful test_set
            # stage. The runner must NOT reuse the partial v0001 -- it
            # must allocate a fresh version (v0002 in our case, since
            # v0001 still occupies the slot on disk) and call test_set again.
            seen.clear()
            modules_run2 = self._modules(seen)
            with (
                patch("p2m.runner._load_context", return_value=self._ctx(root)),
                patch("p2m.runner.STAGES", modules_run2),
                patch("sys.__stderr__", new_callable=io.StringIO),
            ):
                code = run_pipeline(config=str(self._ctx(root)["config_path"]))

            self.assertEqual(code, 0)
            self.assertIn("test_set", seen)
            self.assertTrue((test_set_root / "v0002" / "test_set.jsonl").exists())
            self.assertTrue((test_set_root / "v0002" / "artifact.json").exists())


if __name__ == "__main__":
    unittest.main()
