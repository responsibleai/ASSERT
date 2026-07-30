"""Tests for artifact preservation on resume and for schema stamping.

Resume and --force-stage previously deleted inference_set.jsonl and
scores.jsonl outright. Those files are the evaluation evidence, and a config
hash mismatch is not always what the operator intended.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from assert_ai.core.io import (
    ARTIFACT_SCHEMA_VERSION,
    archive_artifact,
    check_artifact_schema,
    read_artifact_schema_version,
    write_artifact_schema,
)


class TestArchiveArtifact:
    def test_missing_file_returns_none(self):
        with TemporaryDirectory() as tmp:
            assert archive_artifact(Path(tmp) / "absent.jsonl", reason="x") is None

    def test_file_is_preserved_not_deleted(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "inference_set.jsonl"
            path.write_text('{"a": 1}\n', encoding="utf-8")

            backup = archive_artifact(path, reason="config changed")

            assert backup is not None
            assert not path.exists()
            assert backup.exists()
            assert backup.read_text(encoding="utf-8") == '{"a": 1}\n'

    def test_backup_sits_beside_the_original(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "scores.jsonl"
            path.write_text("x", encoding="utf-8")
            backup = archive_artifact(path, reason="r")
            assert backup.parent == path.parent
            assert backup.name.startswith("scores.jsonl.")
            assert backup.name.endswith(".bak")

    def test_repeated_archives_do_not_overwrite_each_other(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "scores.jsonl"
            backups = []
            for i in range(3):
                path.write_text(f"run{i}", encoding="utf-8")
                backups.append(archive_artifact(path, reason="r"))
            assert len({b.name for b in backups}) == 3
            assert {b.read_text(encoding="utf-8") for b in backups} == {
                "run0",
                "run1",
                "run2",
            }

    def test_backup_path_is_logged(self, caplog):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "scores.jsonl"
            path.write_text("x", encoding="utf-8")
            with caplog.at_level("INFO"):
                archive_artifact(path, reason="config changed")
            assert "Preserved" in caplog.text
            assert "config changed" in caplog.text

    def test_opt_out_restores_delete_behaviour(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "scores.jsonl"
            path.write_text("x", encoding="utf-8")
            with patch.dict("os.environ", {"ASSERT_DISCARD_STALE_ARTIFACTS": "1"}):
                assert archive_artifact(path, reason="r") is None
            assert not path.exists()
            assert not list(Path(tmp).glob("*.bak"))

    def test_rename_failure_still_removes_and_warns(self, caplog):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "scores.jsonl"
            path.write_text("x", encoding="utf-8")
            with patch.object(Path, "rename", side_effect=OSError("locked")):
                with caplog.at_level("WARNING"):
                    assert archive_artifact(path, reason="r") is None
            assert "Could not preserve" in caplog.text
            assert not path.exists()


class TestArtifactSchema:
    def test_sidecar_records_version(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "scores.jsonl"
            path.write_text("{}\n", encoding="utf-8")
            sidecar = write_artifact_schema(path, artifact="scores")

            assert sidecar.name == "scores.jsonl.schema.json"
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            assert payload["artifact"] == "scores"
            assert payload["schema_version"] == ARTIFACT_SCHEMA_VERSION

    def test_sidecar_does_not_alter_the_artifact(self):
        """A header line would be read as a data row by any older reader."""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "scores.jsonl"
            original = '{"test_case_id": "a"}\n{"test_case_id": "b"}\n'
            path.write_text(original, encoding="utf-8")
            write_artifact_schema(path, artifact="scores")
            assert path.read_text(encoding="utf-8") == original

    def test_unstamped_artifact_is_treated_as_compatible(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "scores.jsonl"
            path.write_text("{}\n", encoding="utf-8")
            assert read_artifact_schema_version(path) is None
            assert check_artifact_schema(path) is True

    def test_newer_schema_warns(self, caplog):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "scores.jsonl"
            path.write_text("{}\n", encoding="utf-8")
            write_artifact_schema(
                path, artifact="scores", version=ARTIFACT_SCHEMA_VERSION + 1
            )
            with caplog.at_level("WARNING"):
                assert check_artifact_schema(path) is False
            assert "understands" in caplog.text

    def test_corrupt_sidecar_is_ignored(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "scores.jsonl"
            path.write_text("{}\n", encoding="utf-8")
            path.with_name(path.name + ".schema.json").write_text(
                "not json", encoding="utf-8"
            )
            assert read_artifact_schema_version(path) is None
            assert check_artifact_schema(path) is True

    def test_manifest_carries_schema_version(self):
        from assert_ai.core.config_model import RunManifest

        payload = RunManifest(started_at="2026-01-01T00:00:00Z").to_dict()
        assert payload["schema_version"] == 1
