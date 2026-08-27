# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Workspace layout and safe path references for application services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from assert_ai.core.runtime_path_policy import RuntimePathPolicy


@dataclass(frozen=True, slots=True)
class WorkspaceService:
    """Canonical workspace roots shared by MCP-facing services."""

    root: Path
    configs_root: Path
    artifacts_root: Path
    results_root: Path
    path_policy: RuntimePathPolicy

    @classmethod
    def create(
        cls,
        root: str | Path,
        *,
        additional_read_roots: Iterable[str | Path] = (),
    ) -> "WorkspaceService":
        workspace_root = Path(root).expanduser().resolve(strict=True)
        configs_root = workspace_root / "evals"
        artifacts_root = workspace_root / "artifacts"
        results_root = artifacts_root / "results"
        policy = RuntimePathPolicy(
            workspace_root=workspace_root,
            config_root=configs_root,
            artifacts_root=artifacts_root,
            results_root=results_root,
            additional_read_roots=tuple(Path(path) for path in additional_read_roots),
            allow_absolute_inputs=False,
            force_managed_outputs=True,
        )
        return cls(
            root=workspace_root,
            configs_root=policy.config_root,
            artifacts_root=policy.artifacts_root,
            results_root=policy.results_root,
            path_policy=policy,
        )

    def resolve_file(self, path: str | Path, *, field_name: str) -> Path:
        """Resolve an existing workspace-contained file."""
        return self.path_policy.resolve_workspace_path(
            path,
            field_name=field_name,
            must_exist=True,
            file_only=True,
        )

    def reference(self, path: str | Path) -> str:
        """Return a workspace-relative, forward-slash reference."""
        resolved = self.path_policy.require_workspace_path(
            path,
            field_name="workspace reference",
        )
        relative = resolved.relative_to(self.root)
        return "." if not relative.parts else relative.as_posix()
