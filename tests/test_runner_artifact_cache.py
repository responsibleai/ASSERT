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
        concept: str = "Travel planner must produce grounded itineraries.",
        run_id: str | None = None,
        design_level_count: int = 3,
        prompt_sample_size: int = 1,
        include_rollout: bool = False,
        include_upstream: bool = True,
    ) -> dict[str, Any]:
        config_path = root / "config.yaml"
        config_path.write_text("suite: suite-a\n", encoding="utf-8")
        stages: list[tuple[str, dict[str, Any]]] = []
        if include_upstream:
            stages.extend(
                [
                    ("policy", {"model": {"name": "azure/gpt-5.4"}, "behavior_count": 2}),
                    ("design", {"model": {"name": "azure/gpt-5.4"}, "level_count": design_level_count}),
                    (
                        "seeds",
                        {
                            "prompt": {
                                "model": {"name": "azure/gpt-5.4"},
                                "sample_size": prompt_sample_size,
                            }
                        },
                    ),
                ]
            )
        if include_rollout:
            stages.append(("rollout", {}))
        return {
            "config_path": config_path,
            "artifacts_root": root / "artifacts",
            "results_dir": root / "results",
            "suite_root": root / "results" / "suite-a",
            "run_root": root / "results" / "suite-a" / run_id if run_id else None,
            "suite_id": "suite-a",
            "run_id": run_id,
            "concept_name": "travel_planner_eval",
            "concept": concept,
            "context": "Travel planner with flight and hotel tools.",
            "factors": [],
            "target": None,
            "evaluation": None,
            "stages": stages,
        }

    def _modules(self, seen: list[str]) -> dict[str, SimpleNamespace]:
        async def policy(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
            seen.append("policy")
            Path(ctx["systematization_path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(ctx["systematization_path"]).write_text("{}", encoding="utf-8")
            Path(ctx["policy_path"]).write_text(
                json.dumps({"concept": {"name": ctx["concept_name"]}, "behaviors": []}),
                encoding="utf-8",
            )
            return {"policy_path": ctx["policy_path"]}

        async def design(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
            seen.append("design")
            Path(ctx["design_path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(ctx["design_path"]).write_text("{}", encoding="utf-8")
            return {"design_path": ctx["design_path"]}

        async def seeds(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
            seen.append("seeds")
            Path(ctx["seeds_path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(ctx["seeds_path"]).write_text(
                '{"kind":"prompt","seed_id":"seed_000001","seed":{"prompt":"hi"}}\n',
                encoding="utf-8",
            )
            return {"seeds_path": ctx["seeds_path"]}

        async def rollout(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
            seen.append(f"rollout:{Path(ctx['seeds_path']).parent.name}")
            run_root = Path(ctx["run_root"])
            run_root.mkdir(parents=True, exist_ok=True)
            transcripts = run_root / "transcripts.jsonl"
            transcripts.write_text('{"kind":"prompt","seed_id":"seed_000001"}\n', encoding="utf-8")
            return {"transcripts_path": str(transcripts)}

        return {
            "policy": SimpleNamespace(SCOPE="suite", SUITE_OUTPUT="policy.json", run=policy),
            "design": SimpleNamespace(SCOPE="suite", SUITE_OUTPUT="design.json", run=design),
            "seeds": SimpleNamespace(SCOPE="suite", SUITE_OUTPUT="seeds.jsonl", run=seeds),
            "rollout": SimpleNamespace(SCOPE="run", SUITE_OUTPUT=None, run=rollout),
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

    def test_identical_inputs_reuse_policy_design_and_seeds(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            seen: list[str] = []
            codes = self._run_with_contexts([self._ctx(root), self._ctx(root)], seen)

            self.assertEqual(codes, [0, 0])
            self.assertEqual(seen, ["policy", "design", "seeds"])
            self.assertTrue((root / "results" / "suite-a" / "artifacts" / "policy" / "v0001" / "policy.json").exists())
            self.assertFalse((root / "results" / "suite-a" / "artifacts" / "policy" / "v0002").exists())

    def test_concept_change_regenerates_all_upstream_artifacts(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            seen: list[str] = []
            codes = self._run_with_contexts(
                [
                    self._ctx(root),
                    self._ctx(root, concept="Changed travel planner concept."),
                ],
                seen,
            )

            self.assertEqual(codes, [0, 0])
            self.assertEqual(seen, ["policy", "design", "seeds", "policy", "design", "seeds"])
            self.assertTrue((root / "results" / "suite-a" / "artifacts" / "policy" / "v0002" / "policy.json").exists())
            self.assertTrue((root / "results" / "suite-a" / "artifacts" / "design" / "v0002" / "design.json").exists())
            self.assertTrue((root / "results" / "suite-a" / "artifacts" / "seeds" / "v0002" / "seeds.jsonl").exists())

    def test_design_config_change_reuses_policy_but_regenerates_design_and_seeds(self) -> None:
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
            self.assertEqual(seen, ["policy", "design", "seeds", "design", "seeds"])
            self.assertFalse((root / "results" / "suite-a" / "artifacts" / "policy" / "v0002").exists())
            self.assertTrue((root / "results" / "suite-a" / "artifacts" / "design" / "v0002" / "design.json").exists())
            self.assertTrue((root / "results" / "suite-a" / "artifacts" / "seeds" / "v0002" / "seeds.jsonl").exists())

    def test_seed_config_change_reuses_policy_and_design_but_regenerates_seeds(self) -> None:
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
            self.assertEqual(seen, ["policy", "design", "seeds", "seeds"])
            self.assertFalse((root / "results" / "suite-a" / "artifacts" / "policy" / "v0002").exists())
            self.assertFalse((root / "results" / "suite-a" / "artifacts" / "design" / "v0002").exists())
            self.assertTrue((root / "results" / "suite-a" / "artifacts" / "seeds" / "v0002" / "seeds.jsonl").exists())

    def test_force_stage_seeds_creates_new_seed_version_only(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            seen: list[str] = []
            codes = self._run_with_contexts(
                [self._ctx(root), self._ctx(root)],
                seen,
                force_stages=[None, ["seeds"]],
            )

            self.assertEqual(codes, [0, 0])
            self.assertEqual(seen, ["policy", "design", "seeds", "seeds"])
            self.assertFalse((root / "results" / "suite-a" / "artifacts" / "policy" / "v0002").exists())
            self.assertFalse((root / "results" / "suite-a" / "artifacts" / "design" / "v0002").exists())
            self.assertTrue((root / "results" / "suite-a" / "artifacts" / "seeds" / "v0002" / "seeds.jsonl").exists())

    def test_force_stage_policy_cascades_through_design_and_seeds(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            seen: list[str] = []
            codes = self._run_with_contexts(
                [self._ctx(root), self._ctx(root)],
                seen,
                force_stages=[None, ["policy"]],
            )

            self.assertEqual(codes, [0, 0])
            self.assertEqual(
                seen,
                ["policy", "design", "seeds", "policy", "design", "seeds"],
            )
            for stage in ("policy", "design", "seeds"):
                self.assertTrue(
                    (root / "results" / "suite-a" / "artifacts" / stage / "v0002").exists(),
                    msg=f"expected v0002 of {stage}",
                )

    def test_force_stage_design_cascades_to_seeds_only(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            seen: list[str] = []
            codes = self._run_with_contexts(
                [self._ctx(root), self._ctx(root)],
                seen,
                force_stages=[None, ["design"]],
            )

            self.assertEqual(codes, [0, 0])
            self.assertEqual(seen, ["policy", "design", "seeds", "design", "seeds"])
            self.assertFalse((root / "results" / "suite-a" / "artifacts" / "policy" / "v0002").exists())
            self.assertTrue((root / "results" / "suite-a" / "artifacts" / "design" / "v0002").exists())
            self.assertTrue((root / "results" / "suite-a" / "artifacts" / "seeds" / "v0002").exists())

    def test_reuse_path_restores_legacy_compatibility_files(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            seen: list[str] = []
            codes_a = self._run_with_contexts([self._ctx(root)], seen)
            self.assertEqual(codes_a, [0])

            suite_root = root / "results" / "suite-a"
            for filename in ("policy.json", "design.json", "seeds.jsonl"):
                (suite_root / filename).unlink(missing_ok=True)

            codes_b = self._run_with_contexts([self._ctx(root)], seen)
            self.assertEqual(codes_b, [0])
            for filename in ("policy.json", "design.json", "seeds.jsonl"):
                self.assertTrue(
                    (suite_root / filename).exists(),
                    msg=f"reuse path failed to restore legacy {filename}",
                )

    def test_rollout_records_seed_artifact_version_and_runs_per_run(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            seen: list[str] = []
            codes = self._run_with_contexts(
                [
                    self._ctx(root, run_id="run-a", include_rollout=True),
                    self._ctx(root, run_id="run-b", include_rollout=True),
                ],
                seen,
            )

            self.assertEqual(codes, [0, 0])
            self.assertEqual(seen, ["policy", "design", "seeds", "rollout:v0001", "rollout:v0001"])
            manifest = json.loads(
                (root / "results" / "suite-a" / "run-b" / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["artifact_versions"]["seeds"]["version"], "v0001")
            self.assertEqual(
                Path(manifest["artifact_versions"]["seeds"]["path"]),
                Path("artifacts") / "seeds" / "v0001" / "seeds.jsonl",
            )
            self.assertNotIn("relative_path", manifest["artifact_versions"]["seeds"])
            self.assertNotIn("relative_metadata_path", manifest["artifact_versions"]["seeds"])

    def test_rollout_only_config_uses_latest_seed_artifact_version(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            seen: list[str] = []
            codes = self._run_with_contexts(
                [
                    self._ctx(root, run_id="run-a", include_rollout=True),
                    self._ctx(root, run_id="run-b", include_rollout=True, include_upstream=False),
                ],
                seen,
            )

            self.assertEqual(codes, [0, 0])
            self.assertEqual(seen, ["policy", "design", "seeds", "rollout:v0001", "rollout:v0001"])
            manifest = json.loads(
                (root / "results" / "suite-a" / "run-b" / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["artifact_versions"]["seeds"]["version"], "v0001")

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

            # Realistic stages that honor raw_cfg["save_dir"] / "save_path",
            # mirroring the actual policy/design/seeds modules. The test then
            # verifies the runner's override takes precedence.
            async def policy(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
                seen.append("policy")
                save_dir = Path(raw_cfg.get("save_dir") or ctx["policy_artifact_dir"])
                save_dir.mkdir(parents=True, exist_ok=True)
                (save_dir / "policy.json").write_text(
                    json.dumps({"concept": {"name": ctx["concept_name"]}, "behaviors": []}),
                    encoding="utf-8",
                )
                (save_dir / "systematization.json").write_text("{}", encoding="utf-8")
                return {"policy_path": str(save_dir / "policy.json")}

            async def design(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
                seen.append("design")
                save_dir = Path(raw_cfg.get("save_dir") or ctx["design_artifact_dir"])
                save_dir.mkdir(parents=True, exist_ok=True)
                (save_dir / "design.json").write_text("{}", encoding="utf-8")
                return {"design_path": str(save_dir / "design.json")}

            async def seeds(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
                seen.append("seeds")
                save_path = Path(raw_cfg.get("save_path") or ctx["seeds_path"])
                save_path.parent.mkdir(parents=True, exist_ok=True)
                save_path.write_text(
                    '{"kind":"prompt","seed_id":"seed_000001","seed":{"prompt":"hi"}}\n',
                    encoding="utf-8",
                )
                return {"seeds_path": str(save_path)}

            modules = {
                "policy": SimpleNamespace(SCOPE="suite", SUITE_OUTPUT="policy.json", run=policy),
                "design": SimpleNamespace(SCOPE="suite", SUITE_OUTPUT="design.json", run=design),
                "seeds": SimpleNamespace(SCOPE="suite", SUITE_OUTPUT="seeds.jsonl", run=seeds),
            }

            ctx = self._ctx(root)
            # Inject user-provided output overrides into the cacheable stages.
            user_policy_dir = root / "user-policy"
            user_design_dir = root / "user-design"
            user_seeds_path = root / "user-seeds" / "out.jsonl"
            ctx["stages"] = [
                ("policy", {"model": {"name": "azure/gpt-5.4"}, "behavior_count": 2, "save_dir": str(user_policy_dir)}),
                ("design", {"model": {"name": "azure/gpt-5.4"}, "level_count": 3, "save_dir": str(user_design_dir)}),
                (
                    "seeds",
                    {
                        "prompt": {"model": {"name": "azure/gpt-5.4"}, "sample_size": 1},
                        "save_path": str(user_seeds_path),
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
            self.assertTrue((suite_artifacts / "policy" / "v0001" / "policy.json").exists())
            self.assertTrue((suite_artifacts / "design" / "v0001" / "design.json").exists())
            self.assertTrue((suite_artifacts / "seeds" / "v0001" / "seeds.jsonl").exists())
            # The user-supplied directories must be ignored entirely.
            self.assertFalse(user_policy_dir.exists())
            self.assertFalse(user_design_dir.exists())
            self.assertFalse(user_seeds_path.exists())

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

        async def failing_design(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
            # Simulate the realistic case where a stage writes partial
            # outputs into its allocated artifact_dir before crashing.
            Path(ctx["design_path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(ctx["design_path"]).write_text(
                '{"partial": true}', encoding="utf-8"
            )
            raise RuntimeError("simulated design failure")

        modules["design"] = SimpleNamespace(
            SCOPE="suite", SUITE_OUTPUT="design.json", run=failing_design
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
            design_root = (
                root / "results" / "suite-a" / "artifacts" / "design"
            )
            # The abandoned v0001 directory must have been removed.
            self.assertFalse((design_root / "v0001").exists())

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
            self.assertTrue((design_root / "v0001" / "design.json").exists())
            self.assertFalse((design_root / "v0002").exists())

    def test_partial_seeds_skips_artifact_finalization(self) -> None:
        """Regression for Jake's review on the absorb of PR #44.

        ``_generate_records`` now tolerates per-batch failures and
        returns a partial ``seeds.jsonl`` plus ``errored_count > 0``.
        Before this fix, the runner finalized the cacheable artifact
        anyway: it wrote ``artifact.json`` next to the partial
        seeds.jsonl, updated ``latest.json``, and a future run with the
        same input hash would silently reuse the smaller-than-requested
        file. This test pins the gate at runner.py: when ``_summary``
        carries a non-zero ``errored_count``, ``finalize_artifact_plan``
        must be skipped so the next run regenerates from scratch.
        """

        modules = self._modules([])

        async def partial_seeds(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
            Path(ctx["seeds_path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(ctx["seeds_path"]).write_text(
                '{"kind":"prompt","seed_id":"seed_000001","seed":{"prompt":"hi"}}\n',
                encoding="utf-8",
            )
            return {
                "seeds_path": ctx["seeds_path"],
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
            modules_run1["seeds"] = SimpleNamespace(
                SCOPE="suite", SUITE_OUTPUT="seeds.jsonl", run=partial_seeds
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
            seeds_root = root / "results" / "suite-a" / "artifacts" / "seeds"

            # The partial seeds.jsonl is preserved in the version dir for
            # inspection -- we don't throw it away just because some
            # batches failed.
            self.assertTrue((seeds_root / "v0001" / "seeds.jsonl").exists())

            # But the artifact.json sidecar must NOT exist: without it,
            # _latest_matching_metadata skips this dir on the next run.
            self.assertFalse((seeds_root / "v0001" / "artifact.json").exists())

            # And latest.json must not point at this version (would be
            # caught by activate_latest_artifacts on a follow-up run).
            latest_path = root / "results" / "suite-a" / "latest.json"
            if latest_path.exists():
                latest = json.loads(latest_path.read_text(encoding="utf-8"))
                self.assertNotIn("seeds", latest.get("artifacts", {}))

            # Re-run with identical inputs but a fully-successful seeds
            # stage. The runner must NOT reuse the partial v0001 -- it
            # must allocate a fresh version (v0002 in our case, since
            # v0001 still occupies the slot on disk) and call seeds again.
            seen.clear()
            modules_run2 = self._modules(seen)
            with (
                patch("p2m.runner._load_context", return_value=self._ctx(root)),
                patch("p2m.runner.STAGES", modules_run2),
                patch("sys.__stderr__", new_callable=io.StringIO),
            ):
                code = run_pipeline(config=str(self._ctx(root)["config_path"]))

            self.assertEqual(code, 0)
            self.assertIn("seeds", seen)
            self.assertTrue((seeds_root / "v0002" / "seeds.jsonl").exists())
            self.assertTrue((seeds_root / "v0002" / "artifact.json").exists())


if __name__ == "__main__":
    unittest.main()
