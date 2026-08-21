"""Tests that a cached artifact altered after creation is not silently reused.

``finalize_artifact_plan`` records a sha256 for every output, but the cache
previously activated a version on an existence check alone, so an artifact
changed after it was written was reused as though it were the computed result.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from assert_ai.core.artifact_cache import (
    _OUTPUT_FILES,
    _metadata_outputs_valid,
    file_sha256,
)

STAGE = "systematize"


def _stage_output_keys() -> list[str]:
    return list(_OUTPUT_FILES.get(STAGE, {}).keys())


def _build_version_dir(root: Path, *, content: str = '{"ok": true}\n') -> dict:
    """Create a version dir with every expected output and matching hashes."""
    version_dir = root / "v0001"
    version_dir.mkdir(parents=True)

    files: dict[str, str] = {}
    file_hashes: dict[str, str] = {}
    for key, filename in _OUTPUT_FILES[STAGE].items():
        path = version_dir / filename
        path.write_text(content, encoding="utf-8")
        files[key] = filename
        file_hashes[key] = file_sha256(path)

    metadata = {"files": files, "file_hashes": file_hashes}
    (version_dir / "artifact.json").write_text(json.dumps(metadata), encoding="utf-8")
    return {"version_dir": version_dir, "metadata": metadata}


@pytest.mark.skipif(not _stage_output_keys(), reason="stage has no declared outputs")
class TestArtifactCacheVerification:
    def test_intact_artifact_is_accepted(self):
        with TemporaryDirectory() as tmp:
            built = _build_version_dir(Path(tmp))
            assert _metadata_outputs_valid(
                STAGE, built["version_dir"], built["metadata"]
            )

    def test_missing_output_is_rejected(self):
        with TemporaryDirectory() as tmp:
            built = _build_version_dir(Path(tmp))
            key = _stage_output_keys()[0]
            (built["version_dir"] / _OUTPUT_FILES[STAGE][key]).unlink()
            assert not _metadata_outputs_valid(
                STAGE, built["version_dir"], built["metadata"]
            )

    def test_tampered_output_is_rejected(self):
        with TemporaryDirectory() as tmp:
            built = _build_version_dir(Path(tmp))
            key = _stage_output_keys()[0]
            target = built["version_dir"] / _OUTPUT_FILES[STAGE][key]
            target.write_text('{"ok": false, "tampered": true}\n', encoding="utf-8")
            assert not _metadata_outputs_valid(
                STAGE, built["version_dir"], built["metadata"]
            )

    def test_tampering_is_logged(self, caplog):
        with TemporaryDirectory() as tmp:
            built = _build_version_dir(Path(tmp))
            key = _stage_output_keys()[0]
            target = built["version_dir"] / _OUTPUT_FILES[STAGE][key]
            target.write_text("tampered\n", encoding="utf-8")
            with caplog.at_level("WARNING"):
                _metadata_outputs_valid(STAGE, built["version_dir"], built["metadata"])
            assert "does not match the hash recorded" in caplog.text

    def test_metadata_without_hashes_falls_back_to_existence(self):
        """Artifacts from before hash recording must stay usable."""
        with TemporaryDirectory() as tmp:
            built = _build_version_dir(Path(tmp))
            metadata = dict(built["metadata"])
            metadata.pop("file_hashes")
            assert _metadata_outputs_valid(STAGE, built["version_dir"], metadata)

    def test_verification_can_be_skipped(self):
        with TemporaryDirectory() as tmp:
            built = _build_version_dir(Path(tmp))
            key = _stage_output_keys()[0]
            target = built["version_dir"] / _OUTPUT_FILES[STAGE][key]
            target.write_text("tampered\n", encoding="utf-8")
            with patch.dict("os.environ", {"ASSERT_SKIP_CACHE_VERIFY": "1"}):
                assert _metadata_outputs_valid(
                    STAGE, built["version_dir"], built["metadata"]
                )
