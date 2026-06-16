# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from assert_ai.local_sandbox_runtime.openclaw_endpoint_bridge import (
    _ACTIVE_OPENCLAW_WORKSPACE,
    _seed_workspace_async,
    _verify_workspace_fidelity_async,
)


class RecordingSandboxClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def exec_async(self, *, command: str, stdin_data: bytes | None = None, timeout: int = 30) -> str:
        self.calls.append({"command": command, "stdin_data": stdin_data, "timeout": timeout})
        return "ok"


class RecordingAdapter:
    def __init__(self) -> None:
        self.sandbox_client = RecordingSandboxClient()


def test_seed_workspace_replaces_active_openclaw_workspace_with_snapshot_tar(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / ".openclaw").mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("agent instructions\n", encoding="utf-8")
    (workspace / ".openclaw" / "workspace-state.json").write_text('{"setupCompletedAt":"done"}\n', encoding="utf-8")
    (workspace / ".git").mkdir()
    (workspace / ".git" / "config").write_text("private remote\n", encoding="utf-8")

    adapter = RecordingAdapter()

    asyncio.run(_seed_workspace_async(adapter, workspace))

    calls = adapter.sandbox_client.calls
    commands = [str(call["command"]) for call in calls]
    assert commands[0] == f"rm -rf {_ACTIVE_OPENCLAW_WORKSPACE} && mkdir -p {_ACTIVE_OPENCLAW_WORKSPACE}"
    assert any(f"tar -xzf - -C {_ACTIVE_OPENCLAW_WORKSPACE}" in command for command in commands)
    assert all("/home/agent/workspace" not in command for command in commands)
    tar_call = next(call for call in calls if "tar -xzf -" in str(call["command"]))
    assert isinstance(tar_call["stdin_data"], bytes)
    assert len(tar_call["stdin_data"]) > 0
    assert all(".git" not in str(call["command"]) for call in calls)


def test_verify_workspace_fidelity_checks_active_workspace_sentinel_hashes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / ".openclaw").mkdir(parents=True)
    agent_text = "agent instructions\n"
    state_text = '{"setupCompletedAt":"done"}\n'
    (workspace / "AGENTS.md").write_text(agent_text, encoding="utf-8")
    (workspace / ".openclaw" / "workspace-state.json").write_text(state_text, encoding="utf-8")

    adapter = RecordingAdapter()

    asyncio.run(_verify_workspace_fidelity_async(adapter, workspace))

    commands = [str(call["command"]) for call in adapter.sandbox_client.calls]
    agent_hash = hashlib.sha256(agent_text.encode("utf-8")).hexdigest()
    state_hash = hashlib.sha256(state_text.encode("utf-8")).hexdigest()
    assert any(f"{_ACTIVE_OPENCLAW_WORKSPACE}/AGENTS.md" in command and agent_hash in command for command in commands)
    assert any(f"{_ACTIVE_OPENCLAW_WORKSPACE}/.openclaw/workspace-state.json" in command and state_hash in command for command in commands)
    assert all("/home/agent/workspace" not in command for command in commands)
