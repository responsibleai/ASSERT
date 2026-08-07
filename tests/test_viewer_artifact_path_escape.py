# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests that artifact path resolution cannot be escaped via a symlink.

``resolveArtifactPath`` compared lexically resolved paths, which handles ``..``
but is blind to symlinks: a link inside the artifacts root pointing outside it
resolves to a path that still looks contained, so its target was served.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.node_runner import node_supports_ts, node_ts_args

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_SRC = ROOT / "viewer" / "src" / "lib" / "server" / "artifacts.ts"
CONFIG_SRC = ROOT / "viewer" / "src" / "lib" / "server" / "config.ts"
TYPES_SRC = ROOT / "viewer" / "src" / "lib" / "types.ts"


def _link_dir(link: Path, target: Path) -> bool:
    """Create a directory link at ``link`` pointing to ``target``.

    Windows requires developer mode or elevation for symlinks, but allows
    directory junctions unprivileged, and ``realpath`` resolves both. Using a
    junction there keeps this assertion exercised on Windows rather than skipped
    on the one platform where the lexical check is easiest to get wrong.
    """
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    return True


@unittest.skipUnless(node_supports_ts(), "node binary lacks TypeScript support (need >= 22.6)")
class ArtifactPathEscapeTest(unittest.TestCase):
    def _harness(self, harness_dir: Path) -> None:
        artifacts_source = (
            ARTIFACTS_SRC.read_text(encoding="utf-8")
            .replace("./config.js", "./config.ts")
            .replace("$lib/types.js", "./types.ts")
        )
        config_source = CONFIG_SRC.read_text(encoding="utf-8").replace(
            "import { env } from '$env/dynamic/private';", "const env = process.env;"
        )
        (harness_dir / "artifacts.ts").write_text(artifacts_source, encoding="utf-8")
        (harness_dir / "config.ts").write_text(config_source, encoding="utf-8")
        (harness_dir / "types.ts").write_text(
            TYPES_SRC.read_text(encoding="utf-8"), encoding="utf-8"
        )

    def _resolve(self, harness_dir: Path, artifacts_root: Path, request: str) -> dict:
        script = textwrap.dedent(
            f"""\
            const {{ resolveArtifactPath }} = await import('./artifacts.ts');
            try {{
                const resolved = resolveArtifactPath({json.dumps(request)});
                console.log(JSON.stringify({{ ok: true, resolved }}));
            }} catch (e) {{
                console.log(JSON.stringify({{ ok: false, message: String(e.message) }}));
            }}
            """
        )
        env = dict(os.environ)
        env["ARTIFACTS_ROOT"] = str(artifacts_root)
        result = subprocess.run(
            ["node", *node_ts_args(), "--input-type=module"],
            input=script,
            text=True,
            capture_output=True,
            cwd=harness_dir,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_parent_traversal_is_rejected(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp:
            base = Path(tmp)
            harness = base / "harness"
            harness.mkdir()
            artifacts = base / "artifacts"
            artifacts.mkdir()
            self._harness(harness)

            outcome = self._resolve(harness, artifacts, "../secret.txt")
            self.assertFalse(outcome["ok"], outcome)
            self.assertIn("escaped artifacts root", outcome["message"])

    def test_contained_path_is_allowed(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp:
            base = Path(tmp)
            harness = base / "harness"
            harness.mkdir()
            artifacts = base / "artifacts"
            (artifacts / "run1").mkdir(parents=True)
            (artifacts / "run1" / "manifest.json").write_text("{}", encoding="utf-8")
            self._harness(harness)

            outcome = self._resolve(harness, artifacts, "run1/manifest.json")
            self.assertTrue(outcome["ok"], outcome)
            self.assertTrue(outcome["resolved"].endswith("manifest.json"))

    def test_symlink_out_of_root_is_rejected(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp:
            base = Path(tmp)
            harness = base / "harness"
            harness.mkdir()
            artifacts = base / "artifacts"
            artifacts.mkdir()

            outside = base / "outside"
            outside.mkdir()
            (outside / "manifest.json").write_text('{"classified": true}', encoding="utf-8")

            if not _link_dir(artifacts / "escape", outside):
                self.skipTest("directory links not permitted on this platform")
            self._harness(harness)

            outcome = self._resolve(harness, artifacts, "escape/manifest.json")
            self.assertFalse(outcome["ok"], outcome)
            self.assertIn("escaped artifacts root", outcome["message"])


if __name__ == "__main__":
    unittest.main()
