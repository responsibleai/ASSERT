"""Helpers for running Node.js + TypeScript snippets from tests.

Node ≥ 23.6 strips TypeScript by default. Node 22.6–23.5 needs
``--experimental-strip-types``. Older nodes can't run these tests at all.
This module feature-detects what the available ``node`` binary supports
once per process so each test file doesn't have to.
"""

from __future__ import annotations

import functools
import subprocess
from pathlib import Path


@functools.lru_cache(maxsize=1)
def node_ts_args() -> tuple[str, ...]:
    """Return CLI flags needed for the local node binary to accept TypeScript."""
    probe = "const x: number = 1; console.log(x);"
    base = ["--input-type=module", "-e", probe]
    for extra in ((), ("--experimental-strip-types",)):
        result = subprocess.run(
            ["node", *extra, *base],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return tuple(extra)
    return ()


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
