# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tool module loading, tool schema derivation, and tool dispatch."""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import inspect
import json
import sys
import types
import uuid
from hashlib import sha1
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints

from assert_ai.core.async_utils import invoke_callable


def _search_roots(config_path: Path | None) -> list[tuple[str, Path]]:
    roots: list[tuple[str, Path]] = []
    if config_path is not None:
        roots.append(("Relative to config", config_path.parent.resolve()))
    roots.append(("Relative to cwd", Path.cwd().resolve()))
    return roots


def _module_path_candidates(module_ref: str, *, config_path: Path | None) -> list[tuple[str, Path]]:
    dotted = Path(*module_ref.split("."))
    file_name = dotted.with_suffix(".py")
    package_init = dotted / "__init__.py"
    candidates: list[tuple[str, Path]] = []
    for label, root in _search_roots(config_path):
        for candidate in (root / file_name, root / package_init):
            if candidate.exists():
                candidates.append((label, candidate))
    return candidates


def _load_module_from_file(module_ref: str, path: Path) -> Any:
    module_name = f"_assert_ai_module_{sha1(str(path).encode('utf-8')).hexdigest()}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load module '{module_ref}' from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def _temporary_sys_path(path: Path, *, config_path: Path | None = None):
    from assert_ai.core.security import validate_sys_path_addition

    validate_sys_path_addition(path, config_path=config_path)
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


def _module_classes(module: Any) -> list[type[Any]]:
    return [
        member
        for _, member in inspect.getmembers(module, inspect.isclass)
        if member.__module__ == module.__name__
    ]


def load_tool_module(module_ref: str, *, config_path: Path | None = None) -> Any:
    from assert_ai.core.security import validate_module_ref

    validate_module_ref(module_ref, config_path=config_path)

    direct_path = Path(module_ref).expanduser()
    if _is_direct_module_path(module_ref):
        if not direct_path.is_absolute() and config_path is not None:
            direct_path = (config_path.parent / direct_path).resolve()
        if not direct_path.exists():
            raise ValueError(f"Tool module path does not exist: {direct_path}")
        # Validate direct path is within workspace
        _validate_module_file_path(direct_path, config_path=config_path)
        return _load_module_from_file(module_ref, direct_path)

    try:
        return importlib.import_module(module_ref)
    except ModuleNotFoundError as exc:
        if not _has_missing_target(exc, module_ref):
            raise
        attempted = ["1. Python path (sys.path)"]
        for label, root in _search_roots(config_path):
            attempted.append(f"{len(attempted) + 1}. {label}: {root}")
            with _temporary_sys_path(root, config_path=config_path):
                try:
                    return importlib.import_module(module_ref)
                except ModuleNotFoundError as retry_exc:
                    if not _has_missing_target(retry_exc, module_ref):
                        raise
        for label, candidate in _module_path_candidates(module_ref, config_path=config_path):
            attempted.append(f"{len(attempted) + 1}. Direct file load: {candidate}")
            return _load_module_from_file(module_ref, candidate)
        searched = "\n  ".join(attempted)
        raise ValueError(
            f"Could not import tool module '{module_ref}'.\n"
            f"Searched:\n  {searched}\n"
            "Hint: ensure the module exists and add __init__.py when importing a package.",
        ) from exc


def _validate_module_file_path(path: Path, *, config_path: Path | None = None) -> None:
    """Validate that a direct module file path is within the workspace."""
    resolved = path.resolve()
    cwd = Path.cwd().resolve()

    # Allow paths within cwd
    try:
        resolved.relative_to(cwd)
        return
    except ValueError:
        pass

    # Allow paths within config directory
    if config_path is not None:
        config_dir = config_path.parent.resolve()
        try:
            resolved.relative_to(config_dir)
            return
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


def inspect_tool_module(module_ref: str, *, config_path: Path | None = None) -> tuple[type[Any], list[dict[str, Any]]]:
    module = load_tool_module(module_ref, config_path=config_path)
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
