"""Helpers for running Node.js + TypeScript snippets from tests.

Node ≥ 23.6 strips TypeScript by default for ``.ts`` file imports. Node
22.6–23.5 needs ``--experimental-strip-types``. Older nodes can't import
``.ts`` files at all, so callers should consult ``node_supports_ts()``
and skip when False. This module probes the available ``node`` binary
once per process by attempting to import a tiny ``.ts`` file via the
same code path used by the tests.
"""

from __future__ import annotations

import functools
import subprocess
import tempfile
import textwrap
from pathlib import Path


_FLAG_VARIANTS: tuple[tuple[str, ...], ...] = ((), ("--experimental-strip-types",))


@functools.lru_cache(maxsize=1)
def _detect() -> tuple[bool, tuple[str, ...]]:
    with tempfile.TemporaryDirectory() as tmp:
        ts_path = Path(tmp) / "probe.ts"
        ts_path.write_text("export const x: number = 1;\n", encoding="utf-8")
        script = textwrap.dedent(
            f"""\
            const m = await import({ts_path.as_uri()!r});
            console.log(m.x);
            """
        )
        for extra in _FLAG_VARIANTS:
            result = subprocess.run(
                ["node", *extra, "--input-type=module"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip() == "1":
                return True, tuple(extra)
    return False, ()


def node_supports_ts() -> bool:
    """True if the local ``node`` binary can import ``.ts`` files."""
    return _detect()[0]


def node_ts_args() -> tuple[str, ...]:
    """Return CLI flags needed for the local node binary to import ``.ts``."""
    return _detect()[1]


def run_node_ts(script: str, *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a TS-flavoured ESM ``script`` under node and return the result."""
    return subprocess.run(
        ["node", *node_ts_args(), "--input-type=module"],
        input=script,
        text=True,
        capture_output=True,
        cwd=cwd,
        check=False,
    )
