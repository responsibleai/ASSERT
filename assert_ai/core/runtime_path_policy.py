# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Workspace-aware runtime path resolution and containment policy.

Containment and link checks in this module are pathname snapshots.  A returned
``Path`` does not pin filesystem objects or make a later open/write atomic.
Callers must use these APIs only with trees that untrusted processes cannot
modify concurrently.  Hostile writable trees require an OS-specific,
handle-relative open API with no-follow semantics, which this module does not
provide.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PureWindowsPath
from typing import Iterable


_WINDOWS_FORBIDDEN_OUTPUT_CHARS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_OUTPUT_STEMS = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CONIN$",
        "CONOUT$",
        *(f"COM{suffix}" for suffix in (*range(1, 10), "¹", "²", "³")),
        *(f"LPT{suffix}" for suffix in (*range(1, 10), "¹", "²", "³")),
    }
)


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
    INVALID_OUTPUT_PATH = "invalid_output_path"


class RuntimePathError(ValueError):
    """Typed path-policy failure suitable for application error mapping."""

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
    path = _comparison_path(path)
    root = _comparison_path(root)
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _comparison_path(path: Path) -> Path:
    """Normalize equivalent Windows extended-length paths for comparison."""
    value = os.path.normpath(os.fspath(path))
    if os.name == "nt":
        value = value.replace("/", "\\")
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
        value = os.path.normcase(value)
    return Path(value)


def _resolved(path: str | Path, *, base: Path | None = None) -> Path:
    candidate = Path(path).expanduser()
    if base is not None and not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve()


def _same_path(left: Path, right: Path) -> bool:
    return _comparison_path(left) == _comparison_path(right)


def _strictly_within(path: Path, root: Path) -> bool:
    return not _same_path(path, root) and _is_within(path, root)


def _paths_overlap(left: Path, right: Path) -> bool:
    return _is_within(left, right) or _is_within(right, left)


def _rebase_within(path: Path, root: Path) -> Path:
    comparison_relative = _comparison_path(path).relative_to(
        _comparison_path(root)
    )
    if not comparison_relative.parts:
        return root
    return root.joinpath(*path.parts[-len(comparison_relative.parts) :])


def _component_matches(left: str, right: str) -> bool:
    if os.name == "nt":
        return os.path.normcase(left) == os.path.normcase(right)
    return left == right


def _windows_unsafe_output_component(path: str | Path) -> str | None:
    """Return the first component that is unsafe under Win32 naming rules."""
    candidate = PureWindowsPath(os.fspath(path))
    if candidate.drive and not candidate.root:
        return candidate.drive
    for component in candidate.parts:
        if component == candidate.anchor or component in {".", ".."}:
            continue
        if component.endswith((" ", ".")):
            return component
        if any(
            ord(character) < 32
            or character in _WINDOWS_FORBIDDEN_OUTPUT_CHARS
            for character in component
        ):
            return component
        stem = component.split(".", maxsplit=1)[0].rstrip(" ").upper()
        if stem in _WINDOWS_RESERVED_OUTPUT_STEMS:
            return component
    return None


def _deduplicate_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    unique: list[Path] = []
    for path in paths:
        if not any(_same_path(path, existing) for existing in unique):
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

        config_root = _resolved(self.config_root, base=workspace_root)
        artifacts_root = _resolved(self.artifacts_root, base=workspace_root)
        results_root = _resolved(self.results_root, base=workspace_root)
        additional_read_roots = _deduplicate_paths(
            _resolved(root, base=workspace_root)
            for root in self.additional_read_roots
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

            if root.exists() and not root.is_dir():
                raise RuntimePathError(
                    RuntimePathErrorCode.INVALID_ROOT,
                    f"{field_name} is not a directory: {root}",
                    field_name=field_name,
                    path=root,
                )

        for index, root in enumerate(additional_read_roots):
            if root.exists() and not root.is_dir():
                field_name = f"additional_read_roots[{index}]"
                raise RuntimePathError(
                    RuntimePathErrorCode.INVALID_ROOT,
                    f"{field_name} is not a directory: {root}",
                    field_name=field_name,
                    path=root,
                )

        if _paths_overlap(config_root, artifacts_root):
            raise RuntimePathError(
                RuntimePathErrorCode.INVALID_ROOT,
                "config_root and artifacts_root must be disjoint",
                field_name="config_root",
                path=config_root,
                expected_root=artifacts_root,
            )

        if not _strictly_within(
            results_root,
            artifacts_root,
        ):
            raise RuntimePathError(
                RuntimePathErrorCode.INVALID_ROOT,
                "results_root must be a strict descendant of artifacts_root",
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
        reject_links: bool = False,
    ) -> Path:
        """Resolve a config path strictly under ``config_root``.

        ``reject_links`` is a pathname snapshot, not authorization for a later
        open against a concurrently mutable tree.
        """
        candidate = Path(path).expanduser()
        if candidate.is_absolute():
            unresolved = candidate
        else:
            parts = candidate.parts
            if parts and _component_matches(parts[0], self.config_root.name):
                candidate = Path(*parts[1:]) if len(parts) > 1 else Path()
            unresolved = self.config_root / candidate
        self._require_within(
            Path(os.path.abspath(unresolved)),
            self.config_root,
            field_name="config",
            code=RuntimePathErrorCode.OUTSIDE_CONFIG_ROOT,
        )
        if reject_links:
            self._require_no_links(
                unresolved,
                self.config_root,
                field_name="config",
            )
        resolved = unresolved.resolve()
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
            root = (
                self.artifacts_root
                if artifact_relative is not None
                else _resolved(base_dir, base=self.workspace_root)
            )
            self._require_within_any_read_root(
                root,
                field_name=f"{field_name} base directory",
            )
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
        """Resolve an output path under the managed artifacts root.

        On Windows, Win32 device names, alternate data streams, forbidden
        characters, controls, and trailing-dot/space aliases are rejected
        before resolution.
        """
        raw_candidate = Path(path).expanduser()
        self._require_valid_output_path(raw_candidate, field_name=field_name)
        candidate = self._output_candidate(raw_candidate)
        self._require_valid_output_path(candidate, field_name=field_name)
        resolved = candidate.resolve()
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
        """Resolve an output within one operation-specific managed root.

        ``reject_links`` performs a best-effort snapshot check of the current
        pathname.  It does not authorize a later write against a directory tree
        that an untrusted process can replace concurrently.
        """
        expected_candidate = Path(expected_root).expanduser()
        self._require_valid_output_path(
            expected_candidate,
            field_name=f"{field_name} expected root",
        )
        if not expected_candidate.is_absolute():
            expected_candidate = self._output_candidate(expected_candidate)
        self._require_valid_output_path(
            expected_candidate,
            field_name=f"{field_name} expected root",
        )
        expected = expected_candidate.resolve()
        raw_candidate = Path(path).expanduser()
        self._require_valid_output_path(raw_candidate, field_name=field_name)
        if (
            raw_candidate.is_absolute()
            or self._artifact_relative(raw_candidate) is not None
        ):
            candidate = self._output_candidate(raw_candidate)
        else:
            candidate = expected / raw_candidate
        self._require_valid_output_path(candidate, field_name=field_name)
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
        resolved = self.require_workspace_path(path, field_name=field_name)
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
        """Snapshot-check an existing managed tree for links or junctions.

        The scan is not atomic with later filesystem operations.  It is suitable
        only for runtime-owned trees that cannot be changed by an adversary
        during or after validation.
        """
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
        """Resolve against the workspace and require the result to remain inside."""
        resolved = _resolved(path, base=self.workspace_root)
        self._require_within(
            resolved,
            self.workspace_root,
            field_name=field_name,
            code=RuntimePathErrorCode.OUTSIDE_WORKSPACE,
        )
        return _rebase_within(resolved, self.workspace_root)

    def workspace_reference(self, path: str | Path) -> str:
        """Return a canonical workspace-relative, forward-slash reference."""
        resolved = self.require_workspace_path(
            path,
            field_name="workspace reference",
        )
        relative = resolved.relative_to(self.workspace_root)
        return "." if not relative.parts else relative.as_posix()

    def module_search_roots(self, config_path: Path | None) -> tuple[tuple[str, Path], ...]:
        """Return the only roots strict dynamic imports may add to ``sys.path``."""
        roots: list[tuple[str, Path]] = []
        if config_path is not None:
            config_dir = self.require_workspace_path(
                config_path.parent,
                field_name="config module root",
            )
            roots.append(("Relative to config", config_dir))
        if not any(_same_path(self.workspace_root, root) for _, root in roots):
            roots.append(("Relative to workspace", self.workspace_root))
        return tuple(roots)

    def require_managed_root(
        self,
        configured: str | Path,
        expected: str | Path,
        *,
        field_name: str,
    ) -> None:
        """Reject a config root override that differs from the managed root."""
        configured_path = _resolved(configured, base=self.workspace_root)
        expected_path = _resolved(expected, base=self.workspace_root)
        if not _same_path(configured_path, expected_path):
            raise RuntimePathError(
                RuntimePathErrorCode.MANAGED_ROOT_OVERRIDE,
                f"{field_name} is managed by the runtime and cannot be overridden",
                field_name=field_name,
                path=configured_path,
                expected_root=expected_path,
            )

    def _artifact_relative(self, path: Path) -> Path | None:
        parts = path.parts
        if not parts or not any(
            _component_matches(parts[0], prefix)
            for prefix in ("artifacts", self.artifacts_root.name)
        ):
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
    def _require_valid_output_path(path: Path, *, field_name: str) -> None:
        if os.name != "nt":
            return
        invalid_component = _windows_unsafe_output_component(path)
        if invalid_component is None:
            return
        raise RuntimePathError(
            RuntimePathErrorCode.INVALID_OUTPUT_PATH,
            (
                f"{field_name} contains a component that is not a valid "
                f"Windows output name: {invalid_component!r}"
            ),
            field_name=field_name,
            path=path,
        )

    @staticmethod
    def _require_no_links(
        path: Path,
        root: Path,
        *,
        field_name: str,
    ) -> None:
        normalized = _comparison_path(Path(os.path.abspath(path)))
        comparison_root = _comparison_path(root)
        try:
            relative = normalized.relative_to(comparison_root)
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
