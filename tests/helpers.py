# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable
from unittest.mock import patch


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _noop_setup(_root: Path) -> None:
    return None


@dataclass(frozen=True)
class StageSmokeCase:
    name: str
    run: Callable[..., Any]
    workflow_patch: str
    cfg_factory: Callable[[Path], dict[str, Any]]
    context_factory: Callable[[Path], dict[str, Any]]
    result_factory: Callable[[Path, dict[str, Any]], Any]
    assert_fn: Callable[[dict[str, Any], Any, Path], None]
    setup_fn: Callable[[Path], None] = _noop_setup


def run_stage_smoke_case(case: StageSmokeCase) -> None:
    with TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        case.setup_fn(root)
        calls: dict[str, Any] = {}

        async def fake_workflow(**kwargs: Any) -> Any:
            calls.update(kwargs)
            return case.result_factory(root, kwargs)

        with patch(case.workflow_patch, new=fake_workflow):
            context = case.context_factory(root)
            result = asyncio.run(case.run(context, case.cfg_factory(root)))

        case.assert_fn(calls, result, root)
