# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import logging
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from p2m.core.artifact_cache import (
    activate_artifact_plan,
    activate_latest_artifacts,
    artifact_ref,
    discard_artifact_plan,
    finalize_artifact_plan,
    hash_payload,
    override_cacheable_output_paths,
    prepare_artifact_plan,
    refresh_compatibility_files,
    _allocate_version_dir,
    _iter_version_dirs,
    _load_json_object,
    _relative_to_suite,
    _resolve_ref_path,
)
from p2m.core.io import write_json


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
            "behavior_name": "travel_planner_eval",
            "behavior": "Travel planner must produce grounded itineraries.",
            "context": "Travel planner with flight and hotel tools.",
            "artifact_versions": {},
        }

    def _finalize_policy(self, ctx: dict, raw_cfg: dict) -> "object":
        plan = prepare_artifact_plan(
            ctx=ctx,
            stage_name="systematize",
            raw_cfg=raw_cfg,
            forced=False,
        )
        activate_artifact_plan(ctx, plan)
        plan.output_paths["taxonomy"].parent.mkdir(parents=True, exist_ok=True)
        plan.output_paths["taxonomy"].write_text('{"behavior_categories":[]}', encoding="utf-8")
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
            raw_cfg = {"model": {"name": "azure/gpt-5.4"}, "behavior_category_count": 5}
            self._finalize_policy(ctx, raw_cfg)

            second_ctx = self._ctx(root)
            second = prepare_artifact_plan(
                ctx=second_ctx,
                stage_name="systematize",
                raw_cfg=raw_cfg,
                forced=False,
            )

            self.assertTrue(second.reused)
            self.assertEqual(second.version, "v0001")

    def test_hash_mismatch_allocates_next_version(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            raw_cfg = {"model": {"name": "azure/gpt-5.4"}, "behavior_category_count": 5}
            self._finalize_policy(ctx, raw_cfg)

            changed_ctx = self._ctx(root)
            changed_ctx["behavior"] = "Changed behavior text."
            second = prepare_artifact_plan(
                ctx=changed_ctx,
                stage_name="systematize",
                raw_cfg=raw_cfg,
                forced=False,
            )

            self.assertFalse(second.reused)
            self.assertEqual(second.version, "v0002")

    def test_revert_to_prior_config_reuses_existing_version(self) -> None:
        """v0001 -> change behavior -> v0002 -> revert -> reuse v0001 (not v0002)."""

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            base_ctx = self._ctx(root)
            raw_cfg = {"model": {"name": "azure/gpt-5.4"}, "behavior_category_count": 5}

            self._finalize_policy(base_ctx, raw_cfg)

            changed_ctx = self._ctx(root)
            changed_ctx["behavior"] = "Changed behavior text."
            self._finalize_policy(changed_ctx, raw_cfg)

            reverted_ctx = self._ctx(root)
            reverted_plan = prepare_artifact_plan(
                ctx=reverted_ctx,
                stage_name="systematize",
                raw_cfg=raw_cfg,
                forced=False,
            )

            self.assertTrue(reverted_plan.reused)
            self.assertEqual(reverted_plan.version, "v0001")

    def test_finalize_omits_behavior_hash_for_non_policy_stages(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            raw_cfg_policy = {"model": {"name": "azure/gpt-5.4"}, "behavior_category_count": 2}
            self._finalize_policy(ctx, raw_cfg_policy)

            test_set_plan = prepare_artifact_plan(
                ctx=ctx,
                stage_name="test_set",
                raw_cfg={"prompt": {"model": {"name": "azure/gpt-5.4"}, "sample_size": 1}},
                forced=False,
            )
            activate_artifact_plan(ctx, test_set_plan)
            test_set_plan.output_paths["test_set"].parent.mkdir(parents=True, exist_ok=True)
            test_set_plan.output_paths["test_set"].write_text("", encoding="utf-8")
            test_set_plan.output_paths["stratification"].write_text("{}", encoding="utf-8")
            ref = finalize_artifact_plan(ctx, test_set_plan)

            metadata = (test_set_plan.artifact_dir / "artifact.json").read_text(encoding="utf-8")
            self.assertNotIn("behavior_hash", metadata)
            self.assertNotIn("behavior_hash", ref)

    def test_artifact_ref_does_not_emit_relative_path_aliases(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            raw_cfg = {"model": {"name": "azure/gpt-5.4"}, "behavior_category_count": 2}
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
            outside = root / "elsewhere" / "taxonomy.json"
            outside.parent.mkdir(parents=True, exist_ok=True)
            outside.write_text("{}", encoding="utf-8")

            relative = _relative_to_suite(outside, suite_root)
            self.assertNotIn("\\", relative)
            self.assertTrue(relative.startswith("../") or relative.endswith("taxonomy.json"))

    def test_activate_latest_recovers_when_referenced_artifact_dir_is_missing(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            raw_cfg = {"model": {"name": "azure/gpt-5.4"}, "behavior_category_count": 2}
            v0001_plan = self._finalize_policy(ctx, raw_cfg)

            changed_ctx = self._ctx(root)
            changed_ctx["behavior"] = "Changed behavior text."
            v0002_plan = self._finalize_policy(changed_ctx, raw_cfg)

            # latest.json now points at v0002. Wipe v0002 to simulate a stale pointer.
            import shutil
            shutil.rmtree(v0002_plan.artifact_dir)

            recovery_ctx = self._ctx(root)
            activate_latest_artifacts(recovery_ctx)

            recovered = recovery_ctx.get("artifact_versions", {}).get("systematize")
            self.assertIsNotNone(recovered)
            self.assertEqual(recovered["version"], v0001_plan.version)

    def test_activate_latest_skips_stage_when_no_valid_version_remains(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            raw_cfg = {"model": {"name": "azure/gpt-5.4"}, "behavior_category_count": 2}
            self._finalize_policy(ctx, raw_cfg)

            import shutil
            shutil.rmtree(Path(ctx["suite_root"]) / "artifacts" / "systematize")

            recovery_ctx = self._ctx(root)
            activate_latest_artifacts(recovery_ctx)
            self.assertNotIn("systematize", recovery_ctx.get("artifact_versions", {}))

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
            raw_cfg = {"model": {"name": "azure/gpt-5.4"}, "behavior_category_count": 2}
            plan = self._finalize_policy(ctx, raw_cfg)

            suite_root = Path(ctx["suite_root"])
            latest_path = suite_root / "latest.json"
            latest = _json.loads(latest_path.read_text(encoding="utf-8"))
            policy_ref = latest["artifacts"]["systematize"]
            policy_ref["artifact_dir"] = "artifacts/systematize/MISSING"
            policy_ref["metadata_path"] = "artifacts/systematize/MISSING/artifact.json"
            latest_path.write_text(_json.dumps(latest), encoding="utf-8")

            recovery_ctx = self._ctx(root)
            activate_latest_artifacts(recovery_ctx)

            recovered = recovery_ctx.get("artifact_versions", {}).get("systematize")
            self.assertIsNotNone(recovered)
            self.assertEqual(recovered["version"], plan.version)
            self.assertNotIn("MISSING", recovered.get("artifact_dir", ""))
            self.assertNotIn("MISSING", recovered.get("metadata_path", ""))
            # The corrected ref must also be persisted to latest.json so the
            # next run does not have to re-recover.
            persisted = _json.loads(latest_path.read_text(encoding="utf-8"))
            persisted_ref = persisted["artifacts"]["systematize"]
            self.assertNotIn("MISSING", persisted_ref.get("artifact_dir", ""))
            self.assertNotIn("MISSING", persisted_ref.get("metadata_path", ""))

    def test_activate_latest_handles_metadata_missing_primary_output_key(self) -> None:
        """Regression for Copilot review (round 4).

        ``_metadata_output_paths`` previously returned only the keys explicitly
        listed in ``metadata['files']``. If the primary output key (e.g.
        ``"taxonomy"``) was missing from a corrupt or legacy metadata file,
        ``activate_latest_artifacts`` would raise ``KeyError`` while indexing
        ``output_paths[next(iter(_OUTPUT_FILES[stage_name]))]``. The merged
        path map must always include every expected key so recovery never
        crashes on partial metadata.
        """

        import json as _json

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            raw_cfg = {"model": {"name": "azure/gpt-5.4"}, "behavior_category_count": 2}
            plan = self._finalize_policy(ctx, raw_cfg)

            metadata_path = plan.artifact_dir / "artifact.json"
            metadata = _json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["files"] = {
                key: value
                for key, value in metadata.get("files", {}).items()
                if key != "taxonomy"
            }
            metadata_path.write_text(_json.dumps(metadata), encoding="utf-8")

            recovery_ctx = self._ctx(root)
            activate_latest_artifacts(recovery_ctx)

            recovered = recovery_ctx.get("artifact_versions", {}).get("systematize")
            self.assertIsNotNone(recovered)
            self.assertEqual(recovered["version"], plan.version)
            self.assertTrue(Path(recovery_ctx["taxonomy_path"]).exists())

    def test_activate_latest_skips_when_canonical_primary_output_missing(self) -> None:
        """Regression for Copilot review (round 4).

        When a metadata file omits the primary output key, the tightened
        ``_metadata_outputs_exist`` must verify the canonical default file
        is present on disk before the artifact is treated as intact, so a
        corrupt artifact missing its primary output cannot be activated.
        """

        import json as _json

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            raw_cfg = {"model": {"name": "azure/gpt-5.4"}, "behavior_category_count": 2}
            plan = self._finalize_policy(ctx, raw_cfg)

            metadata_path = plan.artifact_dir / "artifact.json"
            metadata = _json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["files"] = {
                key: value
                for key, value in metadata.get("files", {}).items()
                if key != "taxonomy"
            }
            metadata_path.write_text(_json.dumps(metadata), encoding="utf-8")
            (plan.artifact_dir / "taxonomy.json").unlink()

            recovery_ctx = self._ctx(root)
            activate_latest_artifacts(recovery_ctx)

            self.assertNotIn("systematize", recovery_ctx.get("artifact_versions", {}))

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
            inside = _resolve_ref_path(suite_root, "artifacts/systematize/v0001/taxonomy.json")
            assert inside is not None
            self.assertEqual(inside, suite_root / "artifacts" / "systematize" / "v0001" / "taxonomy.json")

    def test_override_cacheable_output_paths_redirects_user_save_dir(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            raw_cfg = {
                "model": {"name": "azure/gpt-5.4"},
                "behavior_category_count": 2,
                "save_dir": "user/elsewhere",
            }
            plan = prepare_artifact_plan(
                ctx=ctx,
                stage_name="systematize",
                raw_cfg=raw_cfg,
                forced=False,
            )
            activate_artifact_plan(ctx, plan)
            overridden = override_cacheable_output_paths("systematize", raw_cfg, plan)

            self.assertNotEqual(overridden["save_dir"], "user/elsewhere")
            self.assertEqual(Path(overridden["save_dir"]), plan.artifact_dir)
            # Must be a copy: the original config is left untouched so other
            # references (logs, ctx history) keep the user's value.
            self.assertEqual(raw_cfg["save_dir"], "user/elsewhere")

    def test_override_cacheable_output_paths_redirects_seeds_save_path(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            self._finalize_policy(ctx, {"model": {"name": "azure/gpt-5.4"}, "behavior_category_count": 2})
            test_set_cfg = {
                "prompt": {"model": {"name": "azure/gpt-5.4"}, "sample_size": 1},
                "save_path": "user/elsewhere/test_set.jsonl",
            }
            test_set_plan = prepare_artifact_plan(
                ctx=ctx,
                stage_name="test_set",
                raw_cfg=test_set_cfg,
                forced=False,
            )
            activate_artifact_plan(ctx, test_set_plan)
            overridden = override_cacheable_output_paths("test_set", test_set_cfg, test_set_plan)

            self.assertEqual(Path(overridden["save_path"]), test_set_plan.output_paths["test_set"])
            self.assertEqual(test_set_cfg["save_path"], "user/elsewhere/test_set.jsonl")

    def test_override_cacheable_output_paths_returns_input_for_unknown_stage(self) -> None:
        raw_cfg = {"foo": "bar"}
        # Use a placeholder plan; the function should short-circuit before
        # touching it for non-cacheable stage names.
        from p2m.core.artifact_cache import ArtifactPlan, ArtifactFingerprint

        plan = ArtifactPlan(
            stage_name="inference",
            version="v0001",
            artifact_dir=Path("/tmp"),
            output_paths={},
            fingerprint=ArtifactFingerprint(
                stage_name="inference",
                behavior_hash=None,
                config_hash="x",
                input_hash="y",
                descriptor={},
            ),
            reused=False,
        )
        result = override_cacheable_output_paths("inference", raw_cfg, plan)
        self.assertIs(result, raw_cfg)

    def test_override_cacheable_output_paths_warns_when_user_save_dir_overridden(
        self,
    ) -> None:
        """Regression for Jake's review (round 5).

        Customers who set ``save_dir`` / ``save_path`` in YAML must see a
        warning explaining why their location is being ignored when the
        artifact cache is active for that stage. Silently swapping the value
        is confusing.
        """

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            raw_cfg = {
                "model": {"name": "azure/gpt-5.4"},
                "behavior_category_count": 2,
                "save_dir": "/home/user/my_runs/foo",
            }
            plan = prepare_artifact_plan(
                ctx=ctx,
                stage_name="systematize",
                raw_cfg=raw_cfg,
                forced=False,
            )
            activate_artifact_plan(ctx, plan)

            with self.assertLogs("p2m.core.artifact_cache", level="WARNING") as cm:
                override_cacheable_output_paths("systematize", raw_cfg, plan)

            joined = "\n".join(cm.output)
            self.assertIn("save_dir", joined)
            self.assertIn("/home/user/my_runs/foo", joined)
            self.assertIn("systematize", joined)

    def test_override_cacheable_output_paths_silent_when_no_user_value(
        self,
    ) -> None:
        """When the user did not set save_dir, the override is invisible —
        no spurious warning should appear."""

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            raw_cfg = {"model": {"name": "azure/gpt-5.4"}, "behavior_category_count": 2}
            plan = prepare_artifact_plan(
                ctx=ctx,
                stage_name="systematize",
                raw_cfg=raw_cfg,
                forced=False,
            )
            activate_artifact_plan(ctx, plan)

            logger = logging.getLogger("p2m.core.artifact_cache")
            previous_level = logger.level
            logger.setLevel(logging.WARNING)
            try:
                with self.assertNoLogs("p2m.core.artifact_cache", level="WARNING"):
                    override_cacheable_output_paths("systematize", raw_cfg, plan)
            finally:
                logger.setLevel(previous_level)


class DiscardArtifactPlanTest(unittest.TestCase):
    """Cleanup of allocated-but-not-finalized version directories.

    Regression for Jake's review (round 5): a stage that fails after
    ``prepare_artifact_plan`` allocates ``vNNNN/`` but before
    ``finalize_artifact_plan`` writes the sidecar must not leave a dead
    version directory on disk. Otherwise ``_allocate_version_dir`` keeps
    incrementing past abandoned slots and the stage_root accumulates leaks.
    """

    def _ctx(self, root: Path) -> dict:
        config_path = root / "config.yaml"
        config_path.write_text("suite: suite-a\n", encoding="utf-8")
        suite_root = root / "results" / "suite-a"
        suite_root.mkdir(parents=True, exist_ok=True)
        return {
            "config_path": config_path,
            "artifacts_root": root / "artifacts",
            "suite_root": suite_root,
            "behavior_name": "travel_planner_eval",
            "behavior": "Travel planner must produce grounded itineraries.",
            "context": "Travel planner with flight and hotel tools.",
            "artifact_versions": {},
        }

    def test_discard_removes_partial_version_dir_and_clears_ctx(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            raw_cfg = {"model": {"name": "azure/gpt-5.4"}, "behavior_category_count": 2}
            plan = prepare_artifact_plan(
                ctx=ctx,
                stage_name="systematize",
                raw_cfg=raw_cfg,
                forced=False,
            )
            activate_artifact_plan(ctx, plan)
            plan.artifact_dir.mkdir(parents=True, exist_ok=True)
            (plan.artifact_dir / "taxonomy.json").write_text(
                '{"behavior_categories":["partial"]}', encoding="utf-8"
            )

            self.assertTrue(plan.artifact_dir.exists())
            self.assertIn("systematize", ctx.get("artifact_versions", {}))

            discard_artifact_plan(ctx, plan)

            self.assertFalse(plan.artifact_dir.exists())
            self.assertNotIn("systematize", ctx.get("artifact_versions", {}))

    def test_discard_handles_missing_directory_silently(self) -> None:
        """A sibling cleanup or external rmtree between prepare and discard
        must not raise — discard is a best-effort safety net.

        With atomic allocation, ``prepare_artifact_plan`` always reserves
        ``vNNNN/`` on disk, so to exercise the missing-directory branch we
        explicitly remove it before calling ``discard_artifact_plan``.
        """

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            raw_cfg = {"model": {"name": "azure/gpt-5.4"}, "behavior_category_count": 2}
            plan = prepare_artifact_plan(
                ctx=ctx,
                stage_name="systematize",
                raw_cfg=raw_cfg,
                forced=False,
            )
            activate_artifact_plan(ctx, plan)

            self.assertTrue(plan.artifact_dir.exists())
            shutil.rmtree(plan.artifact_dir)
            self.assertFalse(plan.artifact_dir.exists())

            discard_artifact_plan(ctx, plan)
            self.assertNotIn("systematize", ctx.get("artifact_versions", {}))

    def test_discard_leaves_reused_plan_untouched(self) -> None:
        """A reused plan points at a healthy on-disk artifact predating this
        run. A downstream stage failure must not blow away that cache hit."""

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            raw_cfg = {"model": {"name": "azure/gpt-5.4"}, "behavior_category_count": 2}

            first_plan = prepare_artifact_plan(
                ctx=ctx,
                stage_name="systematize",
                raw_cfg=raw_cfg,
                forced=False,
            )
            activate_artifact_plan(ctx, first_plan)
            first_plan.output_paths["taxonomy"].parent.mkdir(parents=True, exist_ok=True)
            first_plan.output_paths["taxonomy"].write_text(
                '{"behavior_categories":[]}', encoding="utf-8"
            )
            first_plan.output_paths["systematization"].write_text(
                "{}", encoding="utf-8"
            )
            finalize_artifact_plan(ctx, first_plan)

            reused_ctx = self._ctx(root)
            reused_plan = prepare_artifact_plan(
                ctx=reused_ctx,
                stage_name="systematize",
                raw_cfg=raw_cfg,
                forced=False,
            )
            activate_artifact_plan(reused_ctx, reused_plan)
            self.assertTrue(reused_plan.reused)

            discard_artifact_plan(reused_ctx, reused_plan)

            self.assertTrue(reused_plan.artifact_dir.exists())
            self.assertTrue(
                (reused_plan.artifact_dir / "artifact.json").exists()
            )

    def test_next_version_does_not_leak_after_discard(self) -> None:
        """After discard the slot is freed: the next prepare allocates the
        same vNNNN number rather than incrementing past the abandoned one.

        With atomic allocation, ``prepare_artifact_plan`` reserves the
        directory via ``mkdir(exist_ok=False)``; ``discard_artifact_plan``
        ``rmtree``s it; the next ``_allocate_version_dir`` rescan sees no
        ``vNNNN`` and reissues ``v0001``.
        """

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            raw_cfg = {"model": {"name": "azure/gpt-5.4"}, "behavior_category_count": 2}

            first_plan = prepare_artifact_plan(
                ctx=ctx,
                stage_name="systematize",
                raw_cfg=raw_cfg,
                forced=False,
            )
            activate_artifact_plan(ctx, first_plan)
            first_plan.artifact_dir.mkdir(parents=True, exist_ok=True)
            (first_plan.artifact_dir / "taxonomy.json").write_text(
                '{"behavior_categories":["partial"]}', encoding="utf-8"
            )
            self.assertEqual(first_plan.version, "v0001")

            discard_artifact_plan(ctx, first_plan)

            retry_plan = prepare_artifact_plan(
                ctx=ctx,
                stage_name="systematize",
                raw_cfg={**raw_cfg, "behavior_category_count": 5},
                forced=False,
            )
            self.assertEqual(retry_plan.version, "v0001")


class AllocateVersionDirTest(unittest.TestCase):
    """Atomic ``vNNNN`` reservation under simulated concurrency.

    Regression for the race in ``_next_version``: two ``p2m run``
    invocations on the same suite both read ``max(numbers) + 1``, both
    pick the same slot, and silently corrupt each other's outputs.
    ``_allocate_version_dir`` closes the time-of-check/time-of-use window
    by reserving via ``mkdir(exist_ok=False)`` with a retry loop.
    """

    def test_allocates_v0001_on_empty_stage_root(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            stage_root = Path(tmp_dir) / "systematize"
            version, artifact_dir = _allocate_version_dir(stage_root)
            self.assertEqual(version, "v0001")
            self.assertEqual(artifact_dir, stage_root / "v0001")
            self.assertTrue(artifact_dir.exists())

    def test_allocates_after_existing_versions(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            stage_root = Path(tmp_dir) / "systematize"
            stage_root.mkdir(parents=True)
            (stage_root / "v0001").mkdir()
            (stage_root / "v0002").mkdir()

            version, artifact_dir = _allocate_version_dir(stage_root)
            self.assertEqual(version, "v0003")
            self.assertTrue(artifact_dir.exists())

    def test_retries_when_candidate_dir_exists(self) -> None:
        """A concurrent process pre-creates ``v0001`` between our scan and
        our mkdir: the loop must rescan and pick ``v0002`` rather than fail
        or silently reuse the contended slot."""

        from p2m.core import artifact_cache

        with TemporaryDirectory() as tmp_dir:
            stage_root = Path(tmp_dir) / "systematize"
            stage_root.mkdir(parents=True)

            scan_calls = {"n": 0}
            real_iter = artifact_cache._iter_version_dirs

            def staged_iter(path: Path) -> list[Path]:
                scan_calls["n"] += 1
                if scan_calls["n"] == 1:
                    # First scan: report empty. Then race: a sibling pipeline
                    # creates v0001 before our mkdir runs, so our atomic
                    # mkdir(exist_ok=False) must raise FileExistsError and
                    # the loop must retry.
                    (path / "v0001").mkdir()
                    return []
                return real_iter(path)

            with mock.patch.object(
                artifact_cache, "_iter_version_dirs", side_effect=staged_iter
            ):
                version, artifact_dir = _allocate_version_dir(stage_root)

            self.assertEqual(version, "v0002")
            self.assertEqual(scan_calls["n"], 2)
            self.assertTrue((stage_root / "v0001").exists())
            self.assertTrue((stage_root / "v0002").exists())

    def test_raises_after_exhausting_retries(self) -> None:
        """Pathological: every mkdir fails. We give up loudly rather than
        loop forever or return a bogus version."""

        from p2m.core import artifact_cache

        with TemporaryDirectory() as tmp_dir:
            stage_root = Path(tmp_dir) / "systematize"
            stage_root.mkdir(parents=True)

            real_mkdir = Path.mkdir

            def always_collide(self_path, *args, **kwargs):  # type: ignore[no-untyped-def]
                # Only intercept the version-dir mkdir attempts (which pass
                # exist_ok=False). Stage-root creation uses exist_ok=True and
                # must still succeed.
                if (
                    self_path.parent == stage_root
                    and kwargs.get("exist_ok") is False
                ):
                    raise FileExistsError(self_path)
                return real_mkdir(self_path, *args, **kwargs)

            with mock.patch.object(Path, "mkdir", new=always_collide):
                with self.assertRaises(RuntimeError) as cm:
                    _allocate_version_dir(stage_root)

            self.assertIn("could not allocate", str(cm.exception))


class RefreshCompatibilityFilesTest(unittest.TestCase):
    """Suite-root copies must preserve user edits.

    ``refresh_compatibility_files`` runs on every reuse, finalize, and
    activate-latest path. If a user hand-edits ``<suite>/taxonomy.json``
    between runs, the next cache hit must not silently destroy those edits.
    The implementation hashes the destination and only overwrites when
    either (a) it matches the source, or (b) it matches a previously cached
    version's recorded hash (which is what makes ``--force-stage`` continue
    to overwrite cleanly).
    """

    def _ctx(self, root: Path) -> dict:
        suite_root = root / "results" / "suite-a"
        suite_root.mkdir(parents=True, exist_ok=True)
        return {"suite_root": suite_root}

    def _seed_cached_version(
        self,
        suite_root: Path,
        stage_name: str,
        version: str,
        contents: dict[str, tuple[str, str]],
    ) -> Path:
        """Create ``vNNNN/`` with given files plus a sidecar recording their
        hashes. ``contents`` maps output_key -> (filename, payload)."""

        import hashlib

        version_dir = suite_root / "artifacts" / stage_name / version
        version_dir.mkdir(parents=True, exist_ok=True)
        files: dict[str, str] = {}
        file_hashes: dict[str, str] = {}
        for output_key, (filename, payload) in contents.items():
            (version_dir / filename).write_text(payload, encoding="utf-8")
            files[output_key] = filename
            file_hashes[output_key] = hashlib.sha256(
                payload.encode("utf-8")
            ).hexdigest()
        write_json(
            version_dir / "artifact.json",
            {
                "schema_version": 1,
                "artifact_type": stage_name,
                "version": version,
                "files": files,
                "file_hashes": file_hashes,
            },
        )
        return version_dir

    def test_copies_when_destination_missing(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            suite_root = ctx["suite_root"]
            version_dir = self._seed_cached_version(
                suite_root,
                "systematize",
                "v0001",
                {"taxonomy": ("taxonomy.json", '{"a":1}')},
            )

            output_paths = {"taxonomy": version_dir / "taxonomy.json"}
            refresh_compatibility_files(ctx, "systematize", output_paths)

            dest = suite_root / "taxonomy.json"
            self.assertTrue(dest.exists())
            self.assertEqual(dest.read_text(encoding="utf-8"), '{"a":1}')

    def test_no_op_when_destination_matches_source(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            suite_root = ctx["suite_root"]
            version_dir = self._seed_cached_version(
                suite_root,
                "systematize",
                "v0001",
                {"taxonomy": ("taxonomy.json", '{"identical":true}')},
            )
            (suite_root / "taxonomy.json").write_text(
                '{"identical":true}', encoding="utf-8"
            )

            output_paths = {"taxonomy": version_dir / "taxonomy.json"}
            logger = logging.getLogger("p2m.core.artifact_cache")
            previous_level = logger.level
            logger.setLevel(logging.WARNING)
            try:
                with self.assertNoLogs(
                    "p2m.core.artifact_cache", level="WARNING"
                ):
                    refresh_compatibility_files(ctx, "systematize", output_paths)
            finally:
                logger.setLevel(previous_level)

            self.assertEqual(
                (suite_root / "taxonomy.json").read_text(encoding="utf-8"),
                '{"identical":true}',
            )

    def test_preserves_user_edits_and_warns(self) -> None:
        """Regression for the silent-overwrite footgun: a user who hand-edits
        ``<suite>/taxonomy.json`` between runs must see their edit preserved
        and a warning explaining what just happened."""

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            suite_root = ctx["suite_root"]
            version_dir = self._seed_cached_version(
                suite_root,
                "systematize",
                "v0001",
                {"taxonomy": ("taxonomy.json", '{"cached":true}')},
            )
            user_edit = '{"user":"manually edited this between runs"}'
            (suite_root / "taxonomy.json").write_text(user_edit, encoding="utf-8")

            output_paths = {"taxonomy": version_dir / "taxonomy.json"}
            with self.assertLogs(
                "p2m.core.artifact_cache", level="WARNING"
            ) as captured:
                refresh_compatibility_files(ctx, "systematize", output_paths)

            # User's edit preserved on disk.
            self.assertEqual(
                (suite_root / "taxonomy.json").read_text(encoding="utf-8"),
                user_edit,
            )
            joined = "\n".join(captured.output)
            self.assertIn("Preserving local edits", joined)
            self.assertIn("taxonomy.json", joined)
            self.assertIn("--force-stage", joined)

    def test_overwrites_when_destination_matches_prior_cached_version(
        self,
    ) -> None:
        """``--force-stage systematize`` regenerates a fresh ``v0002``. The
        suite-root copy still holds ``v0001`` content (user did not edit it),
        so ``refresh_compatibility_files`` must overwrite it with ``v0002``
        rather than warn-and-skip — the destination matches a cached version,
        which is the signal that it is cache-derived, not user-authored."""

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            suite_root = ctx["suite_root"]
            self._seed_cached_version(
                suite_root,
                "systematize",
                "v0001",
                {"taxonomy": ("taxonomy.json", '{"v":1}')},
            )
            v0002 = self._seed_cached_version(
                suite_root,
                "systematize",
                "v0002",
                {"taxonomy": ("taxonomy.json", '{"v":2}')},
            )
            # Suite-root copy still holds the v0001 payload byte-for-byte
            # (no user edit happened between v0001 finalize and v0002).
            (suite_root / "taxonomy.json").write_text('{"v":1}', encoding="utf-8")

            output_paths = {"taxonomy": v0002 / "taxonomy.json"}
            logger = logging.getLogger("p2m.core.artifact_cache")
            previous_level = logger.level
            logger.setLevel(logging.WARNING)
            try:
                with self.assertNoLogs(
                    "p2m.core.artifact_cache", level="WARNING"
                ):
                    refresh_compatibility_files(ctx, "systematize", output_paths)
            finally:
                logger.setLevel(previous_level)

            self.assertEqual(
                (suite_root / "taxonomy.json").read_text(encoding="utf-8"),
                '{"v":2}',
            )

    def test_per_file_isolation(self) -> None:
        """Multi-output stages: a hand-edit to one file must not block the
        refresh of a sibling file in the same stage."""

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = self._ctx(root)
            suite_root = ctx["suite_root"]
            version_dir = self._seed_cached_version(
                suite_root,
                "systematize",
                "v0001",
                {
                    "taxonomy": ("taxonomy.json", '{"cached":true}'),
                    "systematization": (
                        "systematization.json",
                        '{"cached":"sys"}',
                    ),
                },
            )
            # User edits taxonomy.json but leaves systematization.json alone.
            (suite_root / "taxonomy.json").write_text(
                '{"user":"edit"}', encoding="utf-8"
            )

            output_paths = {
                "taxonomy": version_dir / "taxonomy.json",
                "systematization": version_dir / "systematization.json",
            }
            with self.assertLogs(
                "p2m.core.artifact_cache", level="WARNING"
            ) as captured:
                refresh_compatibility_files(ctx, "systematize", output_paths)

            # User edit on taxonomy.json preserved.
            self.assertEqual(
                (suite_root / "taxonomy.json").read_text(encoding="utf-8"),
                '{"user":"edit"}',
            )
            # systematization.json was missing → copied unconditionally.
            self.assertEqual(
                (suite_root / "systematization.json").read_text(encoding="utf-8"),
                '{"cached":"sys"}',
            )
            # Exactly one warning, scoped to taxonomy.json.
            joined = "\n".join(captured.output)
            self.assertIn("taxonomy.json", joined)
            self.assertNotIn("systematization.json", joined)


if __name__ == "__main__":
    unittest.main()

