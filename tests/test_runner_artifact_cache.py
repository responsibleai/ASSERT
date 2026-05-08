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


if __name__ == "__main__":
    unittest.main()
