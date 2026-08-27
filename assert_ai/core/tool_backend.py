# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tool module loading, tool schema derivation, and tool dispatch."""

from __future__ import annotations

import builtins
import contextlib
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import inspect
import json
import sys
import threading
import types
import uuid
from hashlib import sha1
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union, get_args, get_origin, get_type_hints

from assert_ai.core.async_utils import invoke_callable

if TYPE_CHECKING:
    from assert_ai.core.runtime_path_policy import RuntimePathPolicy


def _search_roots(
    config_path: Path | None,
    path_policy: RuntimePathPolicy | None = None,
) -> list[tuple[str, Path]]:
    if path_policy is not None:
        return list(path_policy.module_search_roots(config_path))
    roots: list[tuple[str, Path]] = []
    if config_path is not None:
        roots.append(("Relative to config", config_path.parent.resolve()))
    roots.append(("Relative to cwd", Path.cwd().resolve()))
    return roots


def _module_path_candidates(
    module_ref: str,
    *,
    config_path: Path | None,
    path_policy: RuntimePathPolicy | None = None,
) -> list[tuple[str, Path]]:
    dotted = Path(*module_ref.split("."))
    file_name = dotted.with_suffix(".py")
    package_init = dotted / "__init__.py"
    candidates: list[tuple[str, Path]] = []
    for label, root in _search_roots(config_path, path_policy):
        for candidate in (root / file_name, root / package_init):
            if candidate.exists():
                candidates.append((label, candidate))
    return candidates


class _WorkspaceSourceLoader(importlib.machinery.SourceFileLoader):
    def __init__(
        self,
        fullname: str,
        path: str,
        finder: "_WorkspaceModuleFinder",
    ) -> None:
        super().__init__(fullname, path)
        self._finder = finder

    def exec_module(self, module: types.ModuleType) -> None:
        workspace_builtins = dict(vars(builtins))
        workspace_builtins["__import__"] = self._finder.import_module
        module.__dict__["__builtins__"] = workspace_builtins
        super().exec_module(module)


class _WorkspaceModuleFinder(importlib.abc.MetaPathFinder):
    def __init__(
        self,
        *,
        namespace: str,
        roots: tuple[Path, ...],
        path_policy: RuntimePathPolicy,
    ) -> None:
        self.namespace = namespace
        self.roots = roots
        self.path_policy = path_policy
        self._importlib_proxy = types.ModuleType("importlib")
        self._importlib_proxy.__dict__.update(importlib.__dict__)
        self._importlib_proxy.import_module = self.import_dynamic_module

    def find_spec(
        self,
        fullname: str,
        path: Any = None,
        target: Any = None,
    ) -> importlib.machinery.ModuleSpec | None:
        del target
        prefix = f"{self.namespace}."
        if not fullname.startswith(prefix):
            return None
        original_name = fullname[len(prefix):]
        leaf_name = original_name.rsplit(".", 1)[-1]
        search_dirs = (
            tuple(Path(value) for value in path)
            if path is not None
            else self.roots
        )
        namespace_dirs: list[Path] = []
        for search_dir in search_dirs:
            module_path = self._validated_path(
                search_dir / f"{leaf_name}.py",
                original_name,
            )
            if module_path.is_file():
                loader = _WorkspaceSourceLoader(
                    fullname,
                    str(module_path),
                    self,
                )
                return importlib.util.spec_from_file_location(
                    fullname,
                    module_path,
                    loader=loader,
                )

            package_dir = self._validated_path(
                search_dir / leaf_name,
                original_name,
            )
            package_init = self._validated_path(
                package_dir / "__init__.py",
                original_name,
            )
            if package_init.is_file():
                loader = _WorkspaceSourceLoader(
                    fullname,
                    str(package_init),
                    self,
                )
                return importlib.util.spec_from_file_location(
                    fullname,
                    package_init,
                    loader=loader,
                    submodule_search_locations=[str(package_dir)],
                )
            if package_dir.is_dir():
                namespace_dirs.append(package_dir)

        if namespace_dirs:
            spec = importlib.machinery.ModuleSpec(
                fullname,
                loader=None,
                is_package=True,
            )
            spec.submodule_search_locations = [
                str(directory) for directory in namespace_dirs
            ]
            return spec
        return None

    def import_module(
        self,
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] | list[str] | None = (),
        level: int = 0,
    ) -> Any:
        from_items = fromlist or ()
        if level != 0 or not name:
            return builtins.__import__(
                name,
                globals,
                locals,
                fromlist,
                level,
            )
        if name == "importlib":
            for item in from_items:
                if (
                    isinstance(item, str)
                    and item != "*"
                    and not hasattr(self._importlib_proxy, item)
                ):
                    try:
                        child = importlib.import_module(f"importlib.{item}")
                    except ModuleNotFoundError:
                        continue
                    setattr(self._importlib_proxy, item, child)
            return self._importlib_proxy
        if name.startswith("importlib."):
            imported = builtins.__import__(
                name,
                globals,
                locals,
                fromlist,
                level,
            )
            if from_items:
                return imported
            child_name = name.split(".", 1)[1].split(".", 1)[0]
            child = sys.modules.get(f"importlib.{child_name}")
            if child is not None:
                setattr(self._importlib_proxy, child_name, child)
            return self._importlib_proxy

        top_level = name.split(".", 1)[0]
        if not self._top_level_exists(top_level):
            return builtins.__import__(
                name,
                globals,
                locals,
                fromlist,
                level,
            )

        mapped_name = f"{self.namespace}.{name}"
        module = importlib.import_module(mapped_name)
        if from_items and getattr(module, "__path__", None) is not None:
            for item in from_items:
                if not isinstance(item, str) or item == "*":
                    continue
                child_name = f"{mapped_name}.{item}"
                if importlib.util.find_spec(child_name) is not None:
                    importlib.import_module(child_name)
        if from_items:
            return module
        return importlib.import_module(f"{self.namespace}.{top_level}")

    def import_dynamic_module(
        self,
        name: str,
        package: str | None = None,
    ) -> types.ModuleType:
        if name.startswith("."):
            mapped_package = package
            if (
                package
                and not package.startswith(f"{self.namespace}.")
                and self._top_level_exists(package.split(".", 1)[0])
            ):
                mapped_package = f"{self.namespace}.{package}"
            return importlib.import_module(name, mapped_package)

        top_level = name.split(".", 1)[0]
        if self._top_level_exists(top_level):
            return importlib.import_module(f"{self.namespace}.{name}")
        return importlib.import_module(name, package)

    def _top_level_exists(self, name: str) -> bool:
        for root in self.roots:
            if (
                self._validated_path(root / f"{name}.py", name).is_file()
                or self._validated_path(root / name / "__init__.py", name).is_file()
                or self._validated_path(root / name, name).is_dir()
            ):
                return True
        return False

    def _validated_path(self, path: Path, module_name: str) -> Path:
        return self.path_policy.resolve_workspace_path(
            path,
            field_name=f"workspace module '{module_name}'",
        )


_WORKSPACE_FINDERS: dict[str, _WorkspaceModuleFinder] = {}
_WORKSPACE_FINDER_LOCK = threading.Lock()


def _workspace_module_namespace(root: Path) -> str:
    root_digest = sha1(str(root.resolve()).encode("utf-8")).hexdigest()
    return f"_assert_ai_workspace_{root_digest}"


def _ensure_workspace_finder(
    *,
    primary_root: Path,
    path_policy: RuntimePathPolicy,
) -> _WorkspaceModuleFinder:
    primary_root = path_policy.require_workspace_path(
        primary_root,
        field_name="module search root",
    )
    roots = [primary_root]
    if path_policy.workspace_root != primary_root:
        roots.append(path_policy.workspace_root)
    namespace = _workspace_module_namespace(primary_root)

    with _WORKSPACE_FINDER_LOCK:
        finder = _WORKSPACE_FINDERS.get(namespace)
        if finder is None:
            finder = _WorkspaceModuleFinder(
                namespace=namespace,
                roots=tuple(roots),
                path_policy=path_policy,
            )
            _WORKSPACE_FINDERS[namespace] = finder
            sys.meta_path.insert(0, finder)
            spec = importlib.machinery.ModuleSpec(
                namespace,
                loader=None,
                is_package=True,
            )
            spec.submodule_search_locations = [str(root) for root in roots]
            package = importlib.util.module_from_spec(spec)
            sys.modules[namespace] = package
    return finder


def _load_module_from_file(
    module_ref: str,
    path: Path,
    *,
    isolated_workspace_import: bool = False,
    package_root: Path | None = None,
    path_policy: RuntimePathPolicy | None = None,
) -> Any:
    if isolated_workspace_import:
        if package_root is None or path_policy is None:
            raise ValueError(
                "An isolated workspace import requires a package root and path policy"
            )
        finder = _ensure_workspace_finder(
            primary_root=package_root,
            path_policy=path_policy,
        )
        module = importlib.import_module(f"{finder.namespace}.{module_ref}")
        module_file = getattr(module, "__file__", None)
        if module_file is None or Path(module_file).resolve() != path.resolve():
            raise ValueError(
                f"Workspace module '{module_ref}' resolved to an unexpected source"
            )
        return module

    module_name = f"_assert_ai_module_{sha1(str(path).encode('utf-8')).hexdigest()}"
    existing = sys.modules.get(module_name)
    if existing is not None:
        existing_file = getattr(existing, "__file__", None)
        if existing_file and Path(existing_file).resolve() == path.resolve():
            return existing
        raise ValueError(
            f"Module name '{module_name}' is already loaded from a different path"
        )
    spec_kwargs = (
        {"submodule_search_locations": [str(path.parent)]}
        if path.name == "__init__.py"
        else {}
    )
    spec = importlib.util.spec_from_file_location(module_name, path, **spec_kwargs)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load module '{module_ref}' from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


@contextlib.contextmanager
def _temporary_sys_path(
    path: Path,
    *,
    config_path: Path | None = None,
    path_policy: RuntimePathPolicy | None = None,
):
    from assert_ai.core.security import validate_sys_path_addition

    validate_sys_path_addition(
        path,
        config_path=config_path,
        path_policy=path_policy,
    )
    sys.path.insert(0, str(path))
    try:
        yield
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(str(path))


def _has_missing_target(exc: ModuleNotFoundError, module_ref: str) -> bool:
    missing_name = exc.name or ""
    return bool(missing_name) and (missing_name == module_ref or module_ref.startswith(f"{missing_name}."))


def _is_direct_module_path(module_ref: str) -> bool:
    return module_ref.endswith((".py", "/__init__.py", "\\__init__.py"))


def _direct_workspace_module(
    path: Path,
    *,
    config_path: Path | None,
    path_policy: RuntimePathPolicy,
) -> tuple[str, Path]:
    for _, root in path_policy.module_search_roots(config_path):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if relative.name == "__init__.py":
            relative = relative.parent
        else:
            relative = relative.with_suffix("")
        if not relative.parts:
            continue
        if any(not part.isidentifier() for part in relative.parts):
            raise ValueError(
                "Strict direct module paths must map to a dotted Python "
                f"module name inside the workspace; got {path}"
            )
        return ".".join(relative.parts), root
    raise ValueError(f"Direct module path is outside the configured module roots: {path}")


def _module_classes(module: Any) -> list[type[Any]]:
    return [
        member
        for _, member in inspect.getmembers(module, inspect.isclass)
        if member.__module__ == module.__name__
    ]


def load_tool_module(
    module_ref: str,
    *,
    config_path: Path | None = None,
    path_policy: RuntimePathPolicy | None = None,
) -> Any:
    from assert_ai.core.security import validate_module_ref

    validate_module_ref(module_ref, config_path=config_path)

    direct_path = Path(module_ref).expanduser()
    if _is_direct_module_path(module_ref):
        if not direct_path.is_absolute():
            if config_path is not None:
                direct_path = (config_path.parent / direct_path).resolve()
            elif path_policy is not None:
                direct_path = (path_policy.workspace_root / direct_path).resolve()
        if not direct_path.exists():
            raise ValueError(f"Tool module path does not exist: {direct_path}")
        # Validate direct path is within workspace
        direct_path = _validate_module_file_path(
            direct_path,
            config_path=config_path,
            path_policy=path_policy,
        )
        if path_policy is not None:
            isolated_ref, package_root = _direct_workspace_module(
                direct_path,
                config_path=config_path,
                path_policy=path_policy,
            )
            return _load_module_from_file(
                isolated_ref,
                direct_path,
                isolated_workspace_import=True,
                package_root=package_root,
                path_policy=path_policy,
            )
        return _load_module_from_file(module_ref, direct_path)

    return _smart_import(
        module_ref,
        config_path=config_path,
        path_policy=path_policy,
        kind="tool module",
    )


def _smart_import(
    module_ref: str,
    *,
    config_path: Path | None,
    path_policy: RuntimePathPolicy | None,
    kind: str,
) -> Any:
    """Import ``module_ref`` with workspace-aware fallback.

    Legacy resolution order:
      1. Standard import via ``sys.path``.
      2. Retry with the config directory temporarily on ``sys.path`` (if known).
      3. Retry with the current working directory temporarily on ``sys.path``.
      4. Direct file load via ``spec_from_file_location`` for ``<root>/<dotted>.py``
         or ``<root>/<dotted>/__init__.py`` under each search root.

    With a strict path policy, only verified source files under the configured
    workspace are loaded. On failure, raises ``ValueError`` listing every
    location that was searched.

    ``kind`` is used only to make the error message specific (e.g. ``"tool module"``,
    ``"callable module"``).
    """
    if path_policy is not None:
        module_parts = module_ref.split(".")
        if not module_parts or any(not part.isidentifier() for part in module_parts):
            raise ValueError(
                f"Strict workspace imports require a dotted Python module name; "
                f"got {module_ref!r}"
            )
        attempted: list[str] = []
        dotted = Path(*module_parts)
        for label, root in _search_roots(config_path, path_policy):
            attempted.append(f"{len(attempted) + 1}. {label}: {root}")
            for candidate in (root / dotted.with_suffix(".py"), root / dotted / "__init__.py"):
                if not candidate.exists():
                    continue
                candidate = path_policy.resolve_workspace_path(
                    candidate,
                    field_name=kind,
                    must_exist=True,
                    file_only=True,
                )
                return _load_module_from_file(
                    module_ref,
                    candidate,
                    isolated_workspace_import=True,
                    package_root=root,
                    path_policy=path_policy,
                )
        searched = "\n  ".join(attempted)
        raise ValueError(
            f"Could not import {kind} '{module_ref}' inside the configured workspace.\n"
            f"Searched:\n  {searched}",
        )

    try:
        return importlib.import_module(module_ref)
    except ModuleNotFoundError as exc:
        if not _has_missing_target(exc, module_ref):
            raise
        attempted = ["1. Python path (sys.path)"]
        for label, root in _search_roots(config_path, path_policy):
            attempted.append(f"{len(attempted) + 1}. {label}: {root}")
            with _temporary_sys_path(
                root,
                config_path=config_path,
                path_policy=path_policy,
            ):
                try:
                    return importlib.import_module(module_ref)
                except ModuleNotFoundError as retry_exc:
                    if not _has_missing_target(retry_exc, module_ref):
                        raise
        for label, candidate in _module_path_candidates(
            module_ref,
            config_path=config_path,
            path_policy=path_policy,
        ):
            attempted.append(f"{len(attempted) + 1}. Direct file load: {candidate}")
            return _load_module_from_file(module_ref, candidate)
        searched = "\n  ".join(attempted)
        raise ValueError(
            f"Could not import {kind} '{module_ref}'.\n"
            f"Searched:\n  {searched}\n"
            "Hint: ensure the module exists and add __init__.py when importing a package.",
        ) from exc


def import_callable_module(
    module_ref: str,
    *,
    config_path: Path | None = None,
    path_policy: RuntimePathPolicy | None = None,
) -> Any:
    """Import the module portion of a ``module.path:function`` callable reference.

    Uses the same workspace-aware fallback as :func:`load_tool_module` so that a
    callable target defined in the user's repo resolves whether the user is
    running ``assert-ai`` from the repo root, from the config directory, or from
    elsewhere. The caller is expected to have validated ``module_ref`` via
    :func:`assert_ai.core.security.validate_callable_ref` first.
    """
    return _smart_import(
        module_ref,
        config_path=config_path,
        path_policy=path_policy,
        kind="callable module",
    )


def _validate_module_file_path(
    path: Path,
    *,
    config_path: Path | None = None,
    path_policy: RuntimePathPolicy | None = None,
) -> Path:
    """Validate that a direct module file path is within the workspace."""
    if path_policy is not None:
        return path_policy.resolve_workspace_path(
            path,
            field_name="tool module path",
            must_exist=True,
            file_only=True,
        )

    resolved = path.resolve()
    cwd = Path.cwd().resolve()

    # Allow paths within cwd
    try:
        resolved.relative_to(cwd)
        return resolved
    except ValueError:
        pass

    # Allow paths within config directory
    if config_path is not None:
        config_dir = config_path.parent.resolve()
        try:
            resolved.relative_to(config_dir)
            return resolved
        except ValueError:
            pass

    raise ValueError(
        f"Tool module path '{resolved}' is outside the workspace. "
        f"Only paths within the working directory or config directory are allowed."
    )


def _matches_scenario_ctor(cls: type[Any]) -> bool:
    try:
        signature = inspect.signature(cls.__init__)
    except (TypeError, ValueError):
        return False
    params = list(signature.parameters.values())[1:]
    if not params:
        return False
    first = params[0]
    return first.name == "scenario"


def _discover_tools_class(module: Any) -> type[Any]:
    named = getattr(module, "Tools", None)
    if inspect.isclass(named):
        return named

    classes = _module_classes(module)
    for cls in classes:
        if _matches_scenario_ctor(cls):
            return cls
    class_names = ", ".join(sorted(cls.__name__ for cls in classes)) or "(none)"
    raise ValueError(
        f"Could not find a Tools class in module '{module.__name__}'. Found classes: {class_names}",
    )


def _parse_arg_descriptions(docstring: str) -> dict[str, str]:
    descriptions: dict[str, str] = {}
    lines = inspect.cleandoc(docstring).splitlines()
    in_args = False
    current_name: str | None = None
    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "Args:":
            in_args = True
            current_name = None
            continue
        if not in_args:
            continue
        if not raw.startswith(" "):
            break
        if ":" in stripped and not stripped.startswith("-"):
            name, desc = stripped.split(":", 1)
            current_name = name.strip()
            descriptions[current_name] = desc.strip()
            continue
        if current_name is not None:
            descriptions[current_name] = f"{descriptions[current_name]} {stripped}".strip()
    return descriptions


def _json_schema_for_annotation(annotation: Any) -> dict[str, Any]:
    origin = get_origin(annotation)
    args = get_args(annotation)

    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation in {dict, dict[str, Any]} or origin is dict:
        return {"type": "object"}
    if annotation in {list, list[Any]} or origin is list:
        item_annotation = args[0] if args else str
        return {"type": "array", "items": _json_schema_for_annotation(item_annotation)}
    if origin is tuple:
        raise ValueError("tuple parameters are not supported; use list[...] instead")
    if origin is None and annotation is Any:
        return {"type": "object"}
    if origin is type(None):
        return {"type": "null"}
    if origin in {types.UnionType, Union} and len(args) == 2 and type(None) in args:
        non_none = args[0] if args[1] is type(None) else args[1]
        return _json_schema_for_annotation(non_none)
    if origin is None:
        raise ValueError(f"unsupported type hint {annotation!r}")
    raise ValueError(f"unsupported type hint {annotation!r}")


def _tool_spec_from_method(method_name: str, method: Any) -> dict[str, Any]:
    signature = inspect.signature(method)
    try:
        type_hints = get_type_hints(method)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Could not resolve type hints for method {method_name}: {exc}") from exc
    docstring = inspect.getdoc(method) or ""
    descriptions = _parse_arg_descriptions(docstring)
    properties: dict[str, Any] = {}
    required: list[str] = []

    for parameter in signature.parameters.values():
        if parameter.name == "self":
            continue
        if parameter.kind == inspect.Parameter.POSITIONAL_ONLY:
            raise ValueError(
                f"Method {method_name}: parameter '{parameter.name}' cannot be positional-only.",
            )
        if parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
            raise ValueError(
                f"Method {method_name}: variadic parameters are not supported.",
            )
        if parameter.name not in type_hints:
            raise ValueError(f"Method {method_name}: parameter '{parameter.name}' has no type hint.")
        schema = _json_schema_for_annotation(type_hints[parameter.name])
        description = descriptions.get(parameter.name)
        if description:
            schema["description"] = description
        properties[parameter.name] = schema
        if parameter.default is inspect._empty:
            required.append(parameter.name)

    description = inspect.cleandoc(docstring).splitlines()[0] if docstring else ""
    return {
        "name": method_name,
        "description": description,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": required,
        },
    }


def _derive_tool_schemas(tools_cls: type[Any]) -> list[dict[str, Any]]:
    reserved_methods = {"open", "close", "session_info"}
    schemas = [
        _tool_spec_from_method(name, member)
        for name, member in inspect.getmembers(tools_cls, predicate=inspect.isfunction)
        if name != "__init__" and not name.startswith("_") and name not in reserved_methods
    ]
    if not schemas:
        raise ValueError(f"{tools_cls.__name__} does not define any public tool methods")
    return schemas


def _serialize_tool_result(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, (dict, list)):
        return json.dumps(result, ensure_ascii=False)
    return str(result)


class ToolBackendResolver:
    def __init__(
        self,
        tools_cls: type[Any],
        scenario: dict[str, Any],
        *,
        tool_timeout_s: float | None = None,
        startup_timeout_s: float | None = None,
    ) -> None:
        self._tools = tools_cls(scenario)
        self._tool_timeout_s = tool_timeout_s
        self._startup_timeout_s = startup_timeout_s
        self._session_id = uuid.uuid4().hex[:12]
        self._lifecycle: dict[str, Any] = {"session_id": self._session_id}

    async def open(self) -> None:
        open_fn = getattr(self._tools, "open", None)
        if not callable(open_fn):
            self._lifecycle["startup"] = {"status": "skipped"}
            return None

        self._lifecycle["startup"] = {"status": "started"}
        try:
            result = await invoke_callable(open_fn, timeout_s=self._startup_timeout_s)
        except Exception as exc:
            self._lifecycle["startup"] = {
                "status": "failed",
                "error": str(exc),
            }
            raise
        self._lifecycle["startup"] = {
            "status": "ok",
            "result": _serialize_lifecycle_result(result),
        }
        return None

    async def close(self) -> None:
        close_fn = getattr(self._tools, "close", None)
        if not callable(close_fn):
            if "shutdown" not in self._lifecycle:
                self._lifecycle["shutdown"] = {"status": "skipped"}
            return None

        self._lifecycle["shutdown"] = {"status": "started"}
        try:
            result = await invoke_callable(close_fn, timeout_s=self._startup_timeout_s)
        except Exception as exc:
            self._lifecycle["shutdown"] = {
                "status": "failed",
                "error": str(exc),
            }
            raise
        self._lifecycle["shutdown"] = {
            "status": "ok",
            "result": _serialize_lifecycle_result(result),
        }
        return None

    def session_metadata(self) -> dict[str, Any]:
        metadata = dict(self._lifecycle)
        session_info = getattr(self._tools, "session_info", None)
        if callable(session_info):
            session_details = _serialize_lifecycle_result(session_info())
            if session_details is not None:
                metadata["session"] = session_details
        return metadata

    async def resolve(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        context: Any | None = None,
    ) -> Any:
        del context
        method = getattr(self._tools, tool_name, None)
        if not callable(method):
            raise ValueError(f"Unknown tool '{tool_name}'")
        result = await invoke_callable(method, timeout_s=self._tool_timeout_s, **tool_args)
        return _resolution(
            _serialize_tool_result(result),
            tool_name,
            session=self.session_metadata(),
        )


def inspect_tool_module(
    module_ref: str,
    *,
    config_path: Path | None = None,
    path_policy: RuntimePathPolicy | None = None,
) -> tuple[type[Any], list[dict[str, Any]]]:
    module = load_tool_module(
        module_ref,
        config_path=config_path,
        path_policy=path_policy,
    )
    tools_cls = _discover_tools_class(module)
    return tools_cls, _derive_tool_schemas(tools_cls)


def _serialize_lifecycle_result(result: Any) -> Any:
    if result is None:
        return None
    if isinstance(result, (str, int, float, bool)):
        return result
    if isinstance(result, (dict, list)):
        return result
    return str(result)


def _resolution(output: str, tool_name: str, *, session: dict[str, Any] | None = None) -> Any:
    from assert_ai.core.session import ToolResolution

    raw = {"call": "tool_module", "tool_name": tool_name}
    if session:
        raw["session"] = session
    return ToolResolution(
        output=output,
        raw=raw,
    )
