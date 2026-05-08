import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from p2m.core.artifact_cache import (
    activate_artifact_plan,
    activate_latest_artifacts,
    artifact_ref,
    finalize_artifact_plan,
    hash_payload,
    override_cacheable_output_paths,
    prepare_artifact_plan,
    _load_json_object,
    _relative_to_suite,
    _resolve_ref_path,
)


class ArtifactCacheTest(unittest.TestCase):
    def _ctx(self, root: Path) -> dict:
        config_path = root / "config.yaml"
        config_path.write_text("suite: suite-a\n", encoding="utf-8")
        suite_root = root / "results" / "suite-a"
        suite_root.mkdir(parents=True, exist_ok=True)
        return {
            "config_path": config_path,
            "artifacts_root": root / "artifacts",
            "suite_root": suite_root,
            "concept_name": "travel_planner_eval",
            "concept": "Travel planner must produce grounded itineraries.",
            "context": "Travel planner with flight and hotel tools.",
            "artifact_versions": {},
        }

    def _finalize_policy(self, ctx: dict, raw_cfg: dict) -> "object":
        plan = prepare_artifact_plan(
            ctx=ctx,
            stage_name="policy",
            raw_cfg=raw_cfg,
            forced=False,
        )
        activate_artifact_plan(ctx, plan)
        plan.output_paths["policy"].parent.mkdir(parents=True, exist_ok=True)
        plan.output_paths["policy"].write_text('{"behaviors":[]}', encoding="utf-8")
        plan.output_paths["systematization"].write_text("{}", encoding="utf-8")
        finalize_artifact_plan(ctx, plan)
        return plan

    def test_hash_payload_is_stable_across_dict_key_order(self) -> None:
        self.assertEqual(
            hash_payload({"b": [2, {"d": 4, "c": 3}], "a": 1}),
            hash_payload({"a": 1, "b": [2, {"c": 3, "d": 4}]}),
        )

    def test_prepare_reuses_latest_matching_artifact(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            raw_cfg = {"model": {"name": "azure/gpt-5.4"}, "behavior_count": 5}
            self._finalize_policy(ctx, raw_cfg)

            second_ctx = self._ctx(root)
            second = prepare_artifact_plan(
                ctx=second_ctx,
                stage_name="policy",
                raw_cfg=raw_cfg,
                forced=False,
            )

            self.assertTrue(second.reused)
            self.assertEqual(second.version, "v0001")

    def test_hash_mismatch_allocates_next_version(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            raw_cfg = {"model": {"name": "azure/gpt-5.4"}, "behavior_count": 5}
            self._finalize_policy(ctx, raw_cfg)

            changed_ctx = self._ctx(root)
            changed_ctx["concept"] = "Changed concept text."
            second = prepare_artifact_plan(
                ctx=changed_ctx,
                stage_name="policy",
                raw_cfg=raw_cfg,
                forced=False,
            )

            self.assertFalse(second.reused)
            self.assertEqual(second.version, "v0002")

    def test_revert_to_prior_config_reuses_existing_version(self) -> None:
        """v0001 -> change concept -> v0002 -> revert -> reuse v0001 (not v0002)."""

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            base_ctx = self._ctx(root)
            raw_cfg = {"model": {"name": "azure/gpt-5.4"}, "behavior_count": 5}

            self._finalize_policy(base_ctx, raw_cfg)

            changed_ctx = self._ctx(root)
            changed_ctx["concept"] = "Changed concept text."
            self._finalize_policy(changed_ctx, raw_cfg)

            reverted_ctx = self._ctx(root)
            reverted_plan = prepare_artifact_plan(
                ctx=reverted_ctx,
                stage_name="policy",
                raw_cfg=raw_cfg,
                forced=False,
            )

            self.assertTrue(reverted_plan.reused)
            self.assertEqual(reverted_plan.version, "v0001")

    def test_finalize_omits_concept_hash_for_non_policy_stages(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            raw_cfg_policy = {"model": {"name": "azure/gpt-5.4"}, "behavior_count": 2}
            self._finalize_policy(ctx, raw_cfg_policy)

            design_plan = prepare_artifact_plan(
                ctx=ctx,
                stage_name="design",
                raw_cfg={"model": {"name": "azure/gpt-5.4"}, "level_count": 3},
                forced=False,
            )
            activate_artifact_plan(ctx, design_plan)
            design_plan.output_paths["design"].parent.mkdir(parents=True, exist_ok=True)
            design_plan.output_paths["design"].write_text("{}", encoding="utf-8")
            ref = finalize_artifact_plan(ctx, design_plan)

            metadata = (design_plan.artifact_dir / "artifact.json").read_text(encoding="utf-8")
            self.assertNotIn("concept_hash", metadata)
            self.assertNotIn("concept_hash", ref)

    def test_artifact_ref_does_not_emit_relative_path_aliases(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            raw_cfg = {"model": {"name": "azure/gpt-5.4"}, "behavior_count": 2}
            plan = self._finalize_policy(ctx, raw_cfg)
            ref = artifact_ref(ctx=ctx, plan=plan, metadata=None)

            self.assertIn("path", ref)
            self.assertIn("metadata_path", ref)
            self.assertNotIn("relative_path", ref)
            self.assertNotIn("relative_metadata_path", ref)
            self.assertNotIn("\\", ref["path"])
            self.assertNotIn("\\", ref["metadata_path"])
            self.assertNotIn("\\", ref["artifact_dir"])

    def test_relative_to_suite_returns_posix_when_path_outside_suite(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            suite_root = root / "results" / "suite-a"
            suite_root.mkdir(parents=True, exist_ok=True)
            outside = root / "elsewhere" / "policy.json"
            outside.parent.mkdir(parents=True, exist_ok=True)
            outside.write_text("{}", encoding="utf-8")

            relative = _relative_to_suite(outside, suite_root)
            self.assertNotIn("\\", relative)
            self.assertTrue(relative.startswith("../") or relative.endswith("policy.json"))

    def test_activate_latest_recovers_when_referenced_artifact_dir_is_missing(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            raw_cfg = {"model": {"name": "azure/gpt-5.4"}, "behavior_count": 2}
            v0001_plan = self._finalize_policy(ctx, raw_cfg)

            changed_ctx = self._ctx(root)
            changed_ctx["concept"] = "Changed concept text."
            v0002_plan = self._finalize_policy(changed_ctx, raw_cfg)

            # latest.json now points at v0002. Wipe v0002 to simulate a stale pointer.
            import shutil
            shutil.rmtree(v0002_plan.artifact_dir)

            recovery_ctx = self._ctx(root)
            activate_latest_artifacts(recovery_ctx)

            recovered = recovery_ctx.get("artifact_versions", {}).get("policy")
            self.assertIsNotNone(recovered)
            self.assertEqual(recovered["version"], v0001_plan.version)

    def test_activate_latest_skips_stage_when_no_valid_version_remains(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            raw_cfg = {"model": {"name": "azure/gpt-5.4"}, "behavior_count": 2}
            self._finalize_policy(ctx, raw_cfg)

            import shutil
            shutil.rmtree(Path(ctx["suite_root"]) / "artifacts" / "policy")

            recovery_ctx = self._ctx(root)
            activate_latest_artifacts(recovery_ctx)
            self.assertNotIn("policy", recovery_ctx.get("artifact_versions", {}))

    def test_activate_latest_rebuilds_ref_when_recorded_paths_are_stale(self) -> None:
        """Regression for Copilot review #001 (round 2).

        When latest.json's ref points at an artifact_dir/metadata_path that no
        longer exists but the version directory itself is intact at the
        canonical location, ``activate_latest_artifacts`` must rebuild the ref
        using the resolved on-disk paths and persist the corrected ref through
        ``update_latest`` instead of silently propagating the stale paths into
        downstream manifests.
        """

        import json as _json

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            raw_cfg = {"model": {"name": "azure/gpt-5.4"}, "behavior_count": 2}
            plan = self._finalize_policy(ctx, raw_cfg)

            suite_root = Path(ctx["suite_root"])
            latest_path = suite_root / "latest.json"
            latest = _json.loads(latest_path.read_text(encoding="utf-8"))
            policy_ref = latest["artifacts"]["policy"]
            policy_ref["artifact_dir"] = "artifacts/policy/MISSING"
            policy_ref["metadata_path"] = "artifacts/policy/MISSING/artifact.json"
            latest_path.write_text(_json.dumps(latest), encoding="utf-8")

            recovery_ctx = self._ctx(root)
            activate_latest_artifacts(recovery_ctx)

            recovered = recovery_ctx.get("artifact_versions", {}).get("policy")
            self.assertIsNotNone(recovered)
            self.assertEqual(recovered["version"], plan.version)
            self.assertNotIn("MISSING", recovered.get("artifact_dir", ""))
            self.assertNotIn("MISSING", recovered.get("metadata_path", ""))
            # The corrected ref must also be persisted to latest.json so the
            # next run does not have to re-recover.
            persisted = _json.loads(latest_path.read_text(encoding="utf-8"))
            persisted_ref = persisted["artifacts"]["policy"]
            self.assertNotIn("MISSING", persisted_ref.get("artifact_dir", ""))
            self.assertNotIn("MISSING", persisted_ref.get("metadata_path", ""))

    def test_load_json_object_ignores_corrupt_json_gracefully(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            corrupt = Path(tmp_dir) / "latest.json"
            corrupt.write_text("{not json", encoding="utf-8")
            self.assertIsNone(_load_json_object(corrupt))

    def test_load_json_object_returns_none_for_non_object_payload(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            list_payload = Path(tmp_dir) / "latest.json"
            list_payload.write_text("[1, 2, 3]", encoding="utf-8")
            self.assertIsNone(_load_json_object(list_payload))

    def test_resolve_ref_path_rejects_parent_segments(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            suite_root = Path(tmp_dir) / "suite"
            suite_root.mkdir()
            self.assertIsNone(_resolve_ref_path(suite_root, "../../etc/passwd"))
            self.assertIsNone(_resolve_ref_path(suite_root, "artifacts/../../escape"))
            inside = _resolve_ref_path(suite_root, "artifacts/policy/v0001/policy.json")
            assert inside is not None
            self.assertEqual(inside, suite_root / "artifacts" / "policy" / "v0001" / "policy.json")

    def test_override_cacheable_output_paths_redirects_user_save_dir(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            raw_cfg = {
                "model": {"name": "azure/gpt-5.4"},
                "behavior_count": 2,
                "save_dir": "user/elsewhere",
            }
            plan = prepare_artifact_plan(
                ctx=ctx,
                stage_name="policy",
                raw_cfg=raw_cfg,
                forced=False,
            )
            activate_artifact_plan(ctx, plan)
            overridden = override_cacheable_output_paths("policy", raw_cfg, plan)

            self.assertNotEqual(overridden["save_dir"], "user/elsewhere")
            self.assertEqual(Path(overridden["save_dir"]), plan.artifact_dir)
            # Must be a copy: the original config is left untouched so other
            # references (logs, ctx history) keep the user's value.
            self.assertEqual(raw_cfg["save_dir"], "user/elsewhere")

    def test_override_cacheable_output_paths_redirects_seeds_save_path(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            self._finalize_policy(ctx, {"model": {"name": "azure/gpt-5.4"}, "behavior_count": 2})
            design_plan = prepare_artifact_plan(
                ctx=ctx,
                stage_name="design",
                raw_cfg={"model": {"name": "azure/gpt-5.4"}, "level_count": 3},
                forced=False,
            )
            activate_artifact_plan(ctx, design_plan)
            design_plan.output_paths["design"].parent.mkdir(parents=True, exist_ok=True)
            design_plan.output_paths["design"].write_text("{}", encoding="utf-8")
            finalize_artifact_plan(ctx, design_plan)

            seeds_cfg = {
                "prompt": {"model": {"name": "azure/gpt-5.4"}, "sample_size": 1},
                "save_path": "user/elsewhere/seeds.jsonl",
            }
            seeds_plan = prepare_artifact_plan(
                ctx=ctx,
                stage_name="seeds",
                raw_cfg=seeds_cfg,
                forced=False,
            )
            activate_artifact_plan(ctx, seeds_plan)
            overridden = override_cacheable_output_paths("seeds", seeds_cfg, seeds_plan)

            self.assertEqual(Path(overridden["save_path"]), seeds_plan.output_paths["seeds"])
            self.assertEqual(seeds_cfg["save_path"], "user/elsewhere/seeds.jsonl")

    def test_override_cacheable_output_paths_returns_input_for_unknown_stage(self) -> None:
        raw_cfg = {"foo": "bar"}
        # Use a placeholder plan; the function should short-circuit before
        # touching it for non-cacheable stage names.
        from p2m.core.artifact_cache import ArtifactPlan, ArtifactFingerprint

        plan = ArtifactPlan(
            stage_name="rollout",
            version="v0001",
            artifact_dir=Path("/tmp"),
            output_paths={},
            fingerprint=ArtifactFingerprint(
                stage_name="rollout",
                concept_hash=None,
                config_hash="x",
                input_hash="y",
                descriptor={},
            ),
            reused=False,
        )
        result = override_cacheable_output_paths("rollout", raw_cfg, plan)
        self.assertIs(result, raw_cfg)


if __name__ == "__main__":
    unittest.main()
