# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Workspace-aware runtime path resolution and containment policy."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Iterable


class RuntimePathErrorCode(StrEnum):
    """Stable machine-readable categories for runtime path failures."""

    INVALID_ROOT = "invalid_root"
    OUTSIDE_CONFIG_ROOT = "outside_config_root"
    OUTSIDE_INPUT_ROOT = "outside_input_root"
    OUTSIDE_WORKSPACE = "outside_workspace"
    OUTSIDE_ARTIFACTS_ROOT = "outside_artifacts_root"
    OUTSIDE_EXPECTED_ROOT = "outside_expected_root"
    MANAGED_ROOT_OVERRIDE = "managed_root_override"
    MANAGED_PATH_LINK = "managed_path_link"
    PATH_NOT_FOUND = "path_not_found"
    NOT_A_FILE = "not_a_file"


class RuntimePathError(ValueError):
    """Typed path-policy failure suitable for CLI and MCP error mapping."""

    def __init__(
        self,
        code: RuntimePathErrorCode,
        message: str,
        *,
        field_name: str,
        path: Path | None = None,
        expected_root: Path | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.field_name = field_name
        self.path = path
        self.expected_root = expected_root


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _deduplicate_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    unique: list[Path] = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return tuple(unique)


@dataclass(frozen=True, slots=True)
class RuntimePathPolicy:
    """Resolve runtime paths against explicit workspace roots."""

    workspace_root: Path
    config_root: Path
    artifacts_root: Path
    results_root: Path
    additional_read_roots: tuple[Path, ...] = ()
    allow_absolute_inputs: bool = False
    force_managed_outputs: bool = True

    def __post_init__(self) -> None:
        workspace_root = _resolved(self.workspace_root)
        if not workspace_root.is_dir():
            raise RuntimePathError(
                RuntimePathErrorCode.INVALID_ROOT,
                f"Workspace root is not a directory: {workspace_root}",
                field_name="workspace_root",
                path=workspace_root,
            )

        config_root = _resolved(self.config_root)
        artifacts_root = _resolved(self.artifacts_root)
        results_root = _resolved(self.results_root)
        additional_read_roots = _deduplicate_paths(
            _resolved(root) for root in self.additional_read_roots
        )

        for field_name, root in (
            ("config_root", config_root),
            ("artifacts_root", artifacts_root),
            ("results_root", results_root),
        ):
            if not _is_within(root, workspace_root):
                raise RuntimePathError(
                    RuntimePathErrorCode.INVALID_ROOT,
                    f"{field_name} must be inside workspace_root",
                    field_name=field_name,
                    path=root,
                    expected_root=workspace_root,
                )

        if self.force_managed_outputs and not _is_within(results_root, artifacts_root):
            raise RuntimePathError(
                RuntimePathErrorCode.INVALID_ROOT,
                "results_root must be inside artifacts_root",
                field_name="results_root",
                path=results_root,
                expected_root=artifacts_root,
            )

        object.__setattr__(self, "workspace_root", workspace_root)
        object.__setattr__(self, "config_root", config_root)
        object.__setattr__(self, "artifacts_root", artifacts_root)
        object.__setattr__(self, "results_root", results_root)
        object.__setattr__(self, "additional_read_roots", additional_read_roots)

    @property
    def read_roots(self) -> tuple[Path, ...]:
        return _deduplicate_paths(
            (
                self.workspace_root,
                self.config_root,
                self.artifacts_root,
                *self.additional_read_roots,
            )
        )

    def resolve_config_path(
        self,
        path: str | Path,
        *,
        must_exist: bool = False,
    ) -> Path:
        """Resolve a config path strictly under ``config_root``."""
        candidate = Path(path).expanduser()
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            parts = candidate.parts
            if parts and parts[0] == self.config_root.name:
                candidate = Path(*parts[1:]) if len(parts) > 1 else Path()
            resolved = (self.config_root / candidate).resolve()
        self._require_within(
            resolved,
            self.config_root,
            field_name="config",
            code=RuntimePathErrorCode.OUTSIDE_CONFIG_ROOT,
        )
        self._require_kind(
            resolved,
            field_name="config",
            must_exist=must_exist,
            file_only=must_exist,
        )
        return resolved

    def resolve_input(
        self,
        path: str | Path,
        *,
        base_dir: Path,
        field_name: str,
        must_exist: bool = False,
        file_only: bool = False,
    ) -> Path:
        """Resolve an input path without allowing relative root escapes."""
        candidate = Path(path).expanduser()
        if candidate.is_absolute():
            resolved = candidate.resolve()
            if not self.allow_absolute_inputs:
                self._require_within_any_read_root(resolved, field_name=field_name)
        else:
            artifact_relative = self._artifact_relative(candidate)
            root = self.artifacts_root if artifact_relative is not None else _resolved(base_dir)
            self._require_within_any_read_root(root, field_name=f"{field_name} base directory")
            suffix = artifact_relative if artifact_relative is not None else candidate
            resolved = (root / suffix).resolve()
            self._require_within(
                resolved,
                root,
                field_name=field_name,
                code=RuntimePathErrorCode.OUTSIDE_INPUT_ROOT,
            )
        self._require_kind(
            resolved,
            field_name=field_name,
            must_exist=must_exist,
            file_only=file_only,
        )
        return resolved

    def resolve_output(
        self,
        path: str | Path,
        *,
        field_name: str,
    ) -> Path:
        """Resolve an output path under the managed artifacts root."""
        resolved = self._output_candidate(path).resolve()
        if self.force_managed_outputs:
            self._require_within(
                resolved,
                self.artifacts_root,
                field_name=field_name,
                code=RuntimePathErrorCode.OUTSIDE_ARTIFACTS_ROOT,
            )
        return resolved

    def resolve_managed_output(
        self,
        path: str | Path,
        *,
        field_name: str,
        expected_root: str | Path,
        reject_links: bool = False,
    ) -> Path:
        """Resolve an output within one operation-specific managed root."""
        expected_candidate = Path(expected_root).expanduser()
        if not expected_candidate.is_absolute():
            expected_candidate = self._output_candidate(expected_candidate)
        expected = expected_candidate.resolve()
        raw_candidate = Path(path).expanduser()
        if (
            raw_candidate.is_absolute()
            or self._artifact_relative(raw_candidate) is not None
        ):
            candidate = self._output_candidate(raw_candidate)
        else:
            candidate = expected / raw_candidate
        self._require_within(
            expected,
            self.artifacts_root,
            field_name=f"{field_name} expected root",
            code=RuntimePathErrorCode.OUTSIDE_ARTIFACTS_ROOT,
        )
        resolved = candidate.resolve()
        self._require_within(
            resolved,
            self.artifacts_root,
            field_name=field_name,
            code=RuntimePathErrorCode.OUTSIDE_ARTIFACTS_ROOT,
        )
        self._require_within(
            resolved,
            expected,
            field_name=field_name,
            code=RuntimePathErrorCode.OUTSIDE_EXPECTED_ROOT,
        )
        if reject_links:
            self._require_no_links(
                expected_candidate,
                self.artifacts_root,
                field_name=f"{field_name} expected root",
            )
            self._require_no_links(
                candidate,
                expected,
                field_name=field_name,
            )
        return resolved

    def resolve_workspace_path(
        self,
        path: str | Path,
        *,
        field_name: str,
        must_exist: bool = False,
        file_only: bool = False,
    ) -> Path:
        """Resolve a path relative to the workspace and keep it contained."""
        candidate = Path(path).expanduser()
        resolved = (
            candidate.resolve()
            if candidate.is_absolute()
            else (self.workspace_root / candidate).resolve()
        )
        self.require_workspace_path(resolved, field_name=field_name)
        self._require_kind(
            resolved,
            field_name=field_name,
            must_exist=must_exist,
            file_only=file_only,
        )
        return resolved

    def require_managed_tree(
        self,
        path: str | Path,
        *,
        field_name: str,
        expected_root: str | Path,
    ) -> Path:
        """Reject links or junctions anywhere in an existing managed tree."""
        root = self.resolve_managed_output(
            path,
            field_name=field_name,
            expected_root=expected_root,
            reject_links=True,
        )
        if not root.is_dir():
            return root
        for current_root, dir_names, file_names in os.walk(
            root,
            followlinks=False,
        ):
            current = Path(current_root)
            for name in (*dir_names, *file_names):
                self.resolve_managed_output(
                    current / name,
                    field_name=f"{field_name} entry",
                    expected_root=root,
                    reject_links=True,
                )
        return root

    def require_workspace_path(self, path: str | Path, *, field_name: str) -> Path:
        """Re-resolve and require a path to remain inside the workspace."""
        resolved = _resolved(path)
        self._require_within(
            resolved,
            self.workspace_root,
            field_name=field_name,
            code=RuntimePathErrorCode.OUTSIDE_WORKSPACE,
        )
        return resolved

    def module_search_roots(self, config_path: Path | None) -> tuple[tuple[str, Path], ...]:
        """Return the only roots strict dynamic imports may add to ``sys.path``."""
        roots: list[tuple[str, Path]] = []
        if config_path is not None:
            config_dir = self.require_workspace_path(
                config_path.parent,
                field_name="config module root",
            )
            roots.append(("Relative to config", config_dir))
        if self.workspace_root not in {root for _, root in roots}:
            roots.append(("Relative to workspace", self.workspace_root))
        return tuple(roots)

    def require_managed_root(
        self,
        configured: Path,
        expected: Path,
        *,
        field_name: str,
    ) -> None:
        """Reject a config root override that differs from the managed root."""
        if configured != expected:
            raise RuntimePathError(
                RuntimePathErrorCode.MANAGED_ROOT_OVERRIDE,
                f"{field_name} is managed by the runtime and cannot be overridden",
                field_name=field_name,
                path=configured,
                expected_root=expected,
            )

    def _artifact_relative(self, path: Path) -> Path | None:
        parts = path.parts
        if not parts or parts[0] not in {"artifacts", self.artifacts_root.name}:
            return None
        return Path(*parts[1:]) if len(parts) > 1 else Path()

    def _output_candidate(self, path: str | Path) -> Path:
        candidate = Path(path).expanduser()
        if candidate.is_absolute():
            return candidate
        artifact_relative = self._artifact_relative(candidate)
        suffix = artifact_relative if artifact_relative is not None else candidate
        return self.artifacts_root / suffix

    @staticmethod
    def _require_no_links(
        path: Path,
        root: Path,
        *,
        field_name: str,
    ) -> None:
        normalized = Path(os.path.abspath(path))
        try:
            relative = normalized.relative_to(root)
        except ValueError:
            return
        current = root
        for part in relative.parts:
            current /= part
            is_junction = getattr(current, "is_junction", None)
            is_reparse_point = False
            if os.name == "nt":
                try:
                    attributes = os.lstat(current).st_file_attributes
                except (AttributeError, FileNotFoundError, OSError):
                    attributes = 0
                is_reparse_point = bool(
                    attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
                )
            if (
                current.is_symlink()
                or (callable(is_junction) and is_junction())
                or is_reparse_point
            ):
                raise RuntimePathError(
                    RuntimePathErrorCode.MANAGED_PATH_LINK,
                    f"{field_name} cannot traverse a symbolic link or junction",
                    field_name=field_name,
                    path=current,
                    expected_root=root,
                )

    def _require_within_any_read_root(self, path: Path, *, field_name: str) -> None:
        if any(_is_within(path, root) for root in self.read_roots):
            return
        raise RuntimePathError(
            RuntimePathErrorCode.OUTSIDE_INPUT_ROOT,
            f"{field_name} is outside the configured read roots",
            field_name=field_name,
            path=path,
        )

    @staticmethod
    def _require_within(
        path: Path,
        root: Path,
        *,
        field_name: str,
        code: RuntimePathErrorCode,
    ) -> None:
        if _is_within(path, root):
            return
        raise RuntimePathError(
            code,
            f"{field_name} escapes its expected root directory",
            field_name=field_name,
            path=path,
            expected_root=root,
        )

    @staticmethod
    def _require_kind(
        path: Path,
        *,
        field_name: str,
        must_exist: bool,
        file_only: bool,
    ) -> None:
        if must_exist and not path.exists():
            raise RuntimePathError(
                RuntimePathErrorCode.PATH_NOT_FOUND,
                f"{field_name} does not exist: {path}",
                field_name=field_name,
                path=path,
            )
        if file_only and path.exists() and not path.is_file():
            raise RuntimePathError(
                RuntimePathErrorCode.NOT_A_FILE,
                f"{field_name} is not a file: {path}",
                field_name=field_name,
                path=path,
            )
