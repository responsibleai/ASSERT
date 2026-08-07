# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Workspace-scoped evaluation config lifecycle."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import re
import time
from bisect import bisect
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field

from assert_ai.config import ConfigError, load_runtime_context, parse_model_config
from assert_ai.core.config_document import (
    ConfigValidationCode,
    ConfigValidationIssue,
    ConfigValidationReport,
    EVAL_CONFIG_SCHEMA_VERSION,
    get_eval_config_json_schema,
    validate_eval_config_document,
)
from assert_ai.core.io import write_text_atomic
from assert_ai.core.runtime_path_policy import RuntimePathError
from assert_ai.core.workspace import WorkspaceService
from assert_ai.services.errors import ServiceError, ServiceErrorCode
from assert_ai.stages import STAGES
from assert_ai.stages.test_set import validate_sampling_config

_CONFIG_SUFFIXES = {".yaml", ".yml"}
_CURSOR_VERSION = 1
_DEFAULT_MAX_CONFIG_BYTES = 1_048_576
_DEFAULT_PAGE_SIZE = 50
_DEFAULT_MAX_PAGE_SIZE = 200
_LOCK_TIMEOUT_S = 10.0


class _ServiceModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class ConfigCatalogEntry(_ServiceModel):
    """Lightweight metadata for one managed config."""

    config_ref: str
    etag: str
    size_bytes: int
    modified_at: str
    structurally_valid: bool


class ConfigPage(_ServiceModel):
    """Bounded page of config catalog entries."""

    items: tuple[ConfigCatalogEntry, ...]
    next_cursor: str | None = None


class ConfigRecord(_ServiceModel):
    """One normalized config and its validation state."""

    config_ref: str
    yaml: str
    document: dict[str, Any]
    etag: str
    validation: ConfigValidationReport


class ConfigSaveResult(_ServiceModel):
    """Identity and optimistic-concurrency token after a save."""

    config_ref: str
    etag: str
    created: bool
    validation: ConfigValidationReport


class ConfigDesignRequest(_ServiceModel):
    """One headless design-agent request."""

    description: str
    model: str = "azure/gpt-5.4-mini"
    seed_config_ref: str | None = None
    seed_yaml: str | None = None
    behavior_preset: str | None = None
    judge_preset: str | None = None
    dimension_hints: str | None = None
    default_model_hint: str | None = None
    max_turns: int = Field(default=5, ge=1, le=100)


class ConfigDraft(_ServiceModel):
    """Model-generated draft that has not been persisted."""

    yaml: str
    document: dict[str, Any]
    validation: ConfigValidationReport


@dataclass(slots=True)
class ConfigService:
    """Read, validate, design, and atomically save managed eval configs."""

    workspace: WorkspaceService
    max_config_bytes: int = _DEFAULT_MAX_CONFIG_BYTES
    default_page_size: int = _DEFAULT_PAGE_SIZE
    max_page_size: int = _DEFAULT_MAX_PAGE_SIZE

    def get_schema(self) -> dict[str, Any]:
        return get_eval_config_json_schema()

    def list_configs(
        self,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> ConfigPage:
        page_size = self._page_size(limit)
        after = _decode_cursor(cursor) if cursor else None
        entries = self._catalog_entries()
        refs = [entry.config_ref for entry in entries]
        start = bisect(refs, after) if after is not None else 0
        page_items = entries[start : start + page_size]
        next_cursor = None
        if start + len(page_items) < len(entries) and page_items:
            next_cursor = _encode_cursor(page_items[-1].config_ref)
        return ConfigPage(items=tuple(page_items), next_cursor=next_cursor)

    def get_config(self, config_ref: str) -> ConfigRecord:
        path = self._resolve_ref(config_ref, must_exist=True, reject_links=True)
        raw_bytes = self._read_bytes(path)
        yaml_text = self._decode(raw_bytes, config_ref=config_ref)
        raw = _load_yaml_mapping(yaml_text)
        normalized = _normalize_yaml(raw)
        return ConfigRecord(
            config_ref=self._config_ref(path),
            yaml=normalized,
            document=raw,
            etag=_etag(raw_bytes),
            validation=self.validate_document(raw, config_ref=self._config_ref(path)),
        )

    def validate_yaml(
        self,
        yaml_text: str,
        *,
        config_ref: str = "draft.yaml",
    ) -> ConfigValidationReport:
        self._check_payload_size(yaml_text.encode("utf-8"))
        try:
            raw = yaml.safe_load(yaml_text)
        except yaml.YAMLError as exc:
            return ConfigValidationReport(
                valid=False,
                issues=(
                    ConfigValidationIssue(
                        code=ConfigValidationCode.INVALID_YAML,
                        path="",
                        message=str(exc),
                    ),
                ),
            )
        return self.validate_document(raw, config_ref=config_ref)

    def validate_document(
        self,
        document: Any,
        *,
        config_ref: str = "draft.yaml",
    ) -> ConfigValidationReport:
        config_path = self._resolve_ref(
            config_ref,
            must_exist=False,
            reject_links=True,
        )
        structural = validate_eval_config_document(document)
        warnings = (
            list(_compatibility_warnings(document))
            if isinstance(document, dict)
            else []
        )
        if not structural.valid:
            return ConfigValidationReport(
                schema_version=structural.schema_version,
                valid=False,
                issues=structural.issues,
                warnings=tuple(warnings),
            )

        assert isinstance(document, dict)
        issues = _stage_semantic_issues(document)
        issues.extend(_dependency_issues(document))
        runtime_document = deepcopy(document)
        try:
            load_runtime_context(
                runtime_document,
                config_path,
                stage_modules=STAGES,
                path_policy=self.workspace.path_policy,
            )
        except RuntimePathError as exc:
            issues.append(
                ConfigValidationIssue(
                    code=ConfigValidationCode.WORKSPACE_VIOLATION,
                    path=_field_name_pointer(exc.field_name),
                    message=(
                        f"{exc.field_name} violates workspace path policy "
                        f"({exc.code.value})"
                    ),
                )
            )
        except (ConfigError, ValueError, FileNotFoundError) as exc:
            issues.append(
                ConfigValidationIssue(
                    code=ConfigValidationCode.SEMANTIC_ERROR,
                    path=_semantic_error_pointer(str(exc)),
                    message=str(exc),
                )
            )

        return ConfigValidationReport(
            schema_version=EVAL_CONFIG_SCHEMA_VERSION,
            valid=not issues,
            issues=tuple(_deduplicate_issues(issues)),
            warnings=tuple(_deduplicate_issues(warnings)),
        )

    def save_config(
        self,
        config_ref: str,
        *,
        yaml_text: str | None = None,
        document: Mapping[str, Any] | None = None,
        expected_etag: str | None = None,
    ) -> ConfigSaveResult:
        if (yaml_text is None) == (document is None):
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "Provide exactly one of yaml_text or document",
            )
        if yaml_text is not None:
            self._check_payload_size(yaml_text.encode("utf-8"))
            try:
                raw = yaml.safe_load(yaml_text)
            except yaml.YAMLError as exc:
                report = ConfigValidationReport(
                    valid=False,
                    issues=(
                        ConfigValidationIssue(
                            code=ConfigValidationCode.INVALID_YAML,
                            path="",
                            message=str(exc),
                        ),
                    ),
                )
                raise _invalid_config_error(report) from exc
        else:
            raw = deepcopy(dict(document or {}))

        report = self.validate_document(raw, config_ref=config_ref)
        if not report.valid:
            raise _invalid_config_error(report)

        assert isinstance(raw, dict)
        try:
            normalized = _normalize_yaml(raw)
        except yaml.YAMLError as exc:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "Config document cannot be serialized as YAML",
            ) from exc
        encoded = normalized.encode("utf-8")
        self._check_payload_size(encoded)
        path = self._resolve_ref(config_ref, must_exist=False, reject_links=True)
        self._ensure_parent(path)

        with self._config_lock(path):
            path = self._resolve_ref(config_ref, must_exist=False, reject_links=True)
            exists = path.is_file()
            current_etag = _etag(self._read_bytes(path)) if exists else None
            if exists and expected_etag is None:
                raise ServiceError(
                    ServiceErrorCode.CONFLICT,
                    "expected_etag is required when replacing an existing config",
                    details={"config_ref": self._config_ref(path)},
                )
            if expected_etag is not None and current_etag != expected_etag:
                raise ServiceError(
                    ServiceErrorCode.STALE_ETAG,
                    "Config changed since it was read",
                    details={
                        "config_ref": self._config_ref(path),
                        "current_etag": current_etag,
                    },
                )
            write_text_atomic(path, normalized)
            saved_etag = _etag(encoded)

        return ConfigSaveResult(
            config_ref=self._config_ref(path),
            etag=saved_etag,
            created=not exists,
            validation=report,
        )

    def design_config(self, request: ConfigDesignRequest) -> ConfigDraft:
        if request.seed_config_ref and request.seed_yaml is not None:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "Provide seed_config_ref or seed_yaml, not both",
            )
        seed_yaml = request.seed_yaml
        validation_ref = "draft.yaml"
        if request.seed_config_ref:
            seed = self.get_config(request.seed_config_ref)
            seed_yaml = seed.yaml
            validation_ref = seed.config_ref
        if seed_yaml is not None:
            self._check_payload_size(seed_yaml.encode("utf-8"))

        from rich.console import Console

        from assert_ai.init._design_agent import run_design_loop

        yaml_result = run_design_loop(
            model=request.model,
            describe=request.description,
            seed_yaml=seed_yaml,
            seed_path=None,
            behavior_preset=request.behavior_preset,
            judge_preset=request.judge_preset,
            dimension_hints=request.dimension_hints,
            default_model_hint=request.default_model_hint,
            non_interactive=True,
            max_turns=request.max_turns,
            console=Console(quiet=True, stderr=True),
            no_color=True,
            save_draft_on_failure=False,
        )
        if yaml_result is None:
            raise ServiceError(
                ServiceErrorCode.CONFIG_INVALID,
                "Design agent did not produce a valid config",
            )
        raw = _load_yaml_mapping(yaml_result)
        normalized = _normalize_yaml(raw)
        return ConfigDraft(
            yaml=normalized,
            document=raw,
            validation=self.validate_document(raw, config_ref=validation_ref),
        )

    def _catalog_entries(self) -> list[ConfigCatalogEntry]:
        root = self.workspace.configs_root
        if not root.exists():
            return []
        entries: list[ConfigCatalogEntry] = []
        for candidate in self._config_paths():
            relative = candidate.relative_to(root).as_posix()
            path = self._resolve_ref(relative, must_exist=True, reject_links=True)
            raw_bytes = self._read_bytes(path)
            try:
                yaml_text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                structurally_valid = False
            else:
                try:
                    decoded = yaml.safe_load(yaml_text)
                except yaml.YAMLError:
                    structurally_valid = False
                else:
                    structurally_valid = validate_eval_config_document(decoded).valid
            stat_result = path.stat()
            entries.append(
                ConfigCatalogEntry(
                    config_ref=self._config_ref(path),
                    etag=_etag(raw_bytes),
                    size_bytes=len(raw_bytes),
                    modified_at=datetime.fromtimestamp(
                        stat_result.st_mtime,
                        tz=timezone.utc,
                    ).isoformat(),
                    structurally_valid=structurally_valid,
                )
            )
        entries.sort(key=lambda entry: entry.config_ref)
        return entries

    def _config_paths(self) -> list[Path]:
        root = self.workspace.configs_root
        pending = [root]
        paths: list[Path] = []
        while pending:
            directory = pending.pop()
            try:
                entries = list(os.scandir(directory))
            except FileNotFoundError:
                continue
            for entry in entries:
                candidate = Path(entry.path)
                relative = candidate.relative_to(root).as_posix()
                try:
                    self.workspace.path_policy.resolve_config_path(
                        relative,
                        reject_links=True,
                    )
                except RuntimePathError as exc:
                    raise ServiceError(
                        ServiceErrorCode.WORKSPACE_VIOLATION,
                        "Managed config tree contains a symbolic link or junction",
                        details={"config_ref": relative},
                    ) from exc
                if entry.is_dir(follow_symlinks=False):
                    pending.append(candidate)
                elif (
                    entry.is_file(follow_symlinks=False)
                    and candidate.suffix.lower() in _CONFIG_SUFFIXES
                ):
                    paths.append(candidate)
        return sorted(paths)

    def _resolve_ref(
        self,
        config_ref: str,
        *,
        must_exist: bool,
        reject_links: bool,
    ) -> Path:
        if not isinstance(config_ref, str) or not config_ref.strip():
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "config_ref must be a non-empty string",
            )
        ref = config_ref.strip().replace("\\", "/")
        if Path(ref).is_absolute():
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "config_ref must be relative to the managed config root",
            )
        if Path(ref).suffix.lower() not in _CONFIG_SUFFIXES:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "config_ref must end in .yaml or .yml",
            )
        try:
            path = self.workspace.path_policy.resolve_config_path(
                ref,
                must_exist=must_exist,
                reject_links=reject_links,
            )
        except RuntimePathError as exc:
            code = (
                ServiceErrorCode.NOT_FOUND
                if exc.code.value in {"path_not_found", "not_a_file"}
                else ServiceErrorCode.WORKSPACE_VIOLATION
            )
            raise ServiceError(code, str(exc)) from exc
        return path

    def _config_ref(self, path: Path) -> str:
        return path.relative_to(self.workspace.configs_root).as_posix()

    def _ensure_parent(self, path: Path) -> None:
        self.workspace.configs_root.mkdir(parents=True, exist_ok=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.path_policy.resolve_config_path(
            path,
            reject_links=True,
        )

    def _read_bytes(self, path: Path) -> bytes:
        try:
            size = path.stat().st_size
        except FileNotFoundError as exc:
            raise ServiceError(ServiceErrorCode.NOT_FOUND, "Config not found") from exc
        if size > self.max_config_bytes:
            raise ServiceError(
                ServiceErrorCode.ARTIFACT_TOO_LARGE,
                f"Config exceeds the {self.max_config_bytes}-byte limit",
            )
        data = path.read_bytes()
        self._check_payload_size(data)
        return data

    def _decode(self, data: bytes, *, config_ref: str) -> str:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ServiceError(
                ServiceErrorCode.CONFIG_INVALID,
                f"Config is not valid UTF-8: {config_ref}",
            ) from exc

    def _check_payload_size(self, data: bytes) -> None:
        if len(data) > self.max_config_bytes:
            raise ServiceError(
                ServiceErrorCode.ARTIFACT_TOO_LARGE,
                f"Config exceeds the {self.max_config_bytes}-byte limit",
            )

    def _page_size(self, value: int | None) -> int:
        size = self.default_page_size if value is None else value
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "limit must be a positive integer",
            )
        if size > self.max_page_size:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                f"limit must be <= {self.max_page_size}",
            )
        return size

    @contextmanager
    def _config_lock(self, path: Path) -> Iterator[None]:
        lock_name = hashlib.sha256(self._config_ref(path).encode("utf-8")).hexdigest()
        lock_ref = f".locks/{lock_name}.lock"
        lock_path = self.workspace.path_policy.resolve_config_path(
            lock_ref,
            reject_links=True,
        )
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.workspace.path_policy.resolve_config_path(
            lock_ref,
            reject_links=True,
        )
        with _exclusive_file_lock(lock_path, timeout_s=_LOCK_TIMEOUT_S):
            yield


def _load_yaml_mapping(yaml_text: str) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise ServiceError(ServiceErrorCode.CONFIG_INVALID, "Invalid YAML") from exc
    if not isinstance(raw, dict):
        raise ServiceError(
            ServiceErrorCode.CONFIG_INVALID,
            "Top-level YAML must be a mapping",
        )
    return raw


def _normalize_yaml(document: Mapping[str, Any]) -> str:
    normalized = yaml.safe_dump(
        dict(document),
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    return normalized if normalized.endswith("\n") else normalized + "\n"


def _etag(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _encode_cursor(config_ref: str) -> str:
    payload = json.dumps(
        {"v": _CURSOR_VERSION, "after": config_ref},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> str:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(cursor + padding).decode("utf-8")
        )
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            "Invalid config cursor",
        ) from exc
    if (
        not isinstance(payload, dict)
        or payload.get("v") != _CURSOR_VERSION
        or not isinstance(payload.get("after"), str)
    ):
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            "Invalid config cursor",
        )
    return payload["after"]


def _compatibility_warnings(
    document: dict[str, Any],
) -> tuple[ConfigValidationIssue, ...]:
    test_set = (document.get("pipeline") or {}).get("test_set")
    if isinstance(test_set, dict) and test_set.get("tool_source") == "per_seed":
        return (
            ConfigValidationIssue(
                code=ConfigValidationCode.DEPRECATED_FIELD,
                path="/pipeline/test_set/tool_source",
                message="per_seed is deprecated; use per_test_case",
            ),
        )
    return ()


def _stage_semantic_issues(
    document: dict[str, Any],
) -> list[ConfigValidationIssue]:
    issues: list[ConfigValidationIssue] = []
    pipeline = document["pipeline"]
    default_model = document.get("default_model")

    systematize = pipeline.get("systematize")
    if isinstance(systematize, dict) and systematize.get("enabled", True):
        if "validators" in systematize or "validator_models" in systematize:
            issues.append(
                _semantic_issue(
                    "/pipeline/systematize",
                    "taxonomy validators are no longer supported",
                )
            )
        _validate_model(
            systematize.get("model") or default_model,
            path="/pipeline/systematize/model",
            required_message="systematize.model or default_model is required",
            issues=issues,
        )

    test_set = pipeline.get("test_set")
    if isinstance(test_set, dict) and test_set.get("enabled", True):
        if "validators" in test_set or "validator_model" in test_set:
            issues.append(
                _semantic_issue(
                    "/pipeline/test_set",
                    "test_set validators are no longer supported",
                )
            )
        prompt = test_set.get("prompt")
        scenario = test_set.get("scenario")
        if not prompt and not scenario:
            issues.append(
                _semantic_issue(
                    "/pipeline/test_set",
                    "test_set requires prompt and/or scenario configuration",
                )
            )
        for kind, raw_kind in (("prompt", prompt), ("scenario", scenario)):
            if not raw_kind:
                continue
            path = f"/pipeline/test_set/{kind}"
            if "budget" in raw_kind:
                issues.append(
                    _semantic_issue(
                        f"{path}/budget",
                        f"test_set.{kind}.budget was renamed to "
                        f"test_set.{kind}.sample_size",
                    )
                )
            if kind == "scenario" and "modality" in raw_kind:
                issues.append(
                    _semantic_issue(
                        f"{path}/modality",
                        "test_set.scenario.modality is no longer supported; "
                        "use test_set.tool_source",
                    )
                )
            _validate_model(
                raw_kind.get("model")
                or test_set.get("model")
                or default_model,
                path=f"{path}/model",
                required_message=f"test_set.{kind}.model is required",
                issues=issues,
            )
            try:
                validate_sampling_config(
                    raw_kind.get("sampling"),
                    field_name=f"test_set.{kind}.sampling",
                )
            except ValueError as exc:
                issues.append(_semantic_issue(f"{path}/sampling", str(exc)))

        stratify = test_set.get("stratify")
        if isinstance(stratify, dict) and stratify.get("model") is not None:
            _validate_model(
                stratify["model"],
                path="/pipeline/test_set/stratify/model",
                required_message="test_set.stratify.model is invalid",
                issues=issues,
            )

    return issues


def _dependency_issues(document: dict[str, Any]) -> list[ConfigValidationIssue]:
    inference = (document.get("pipeline") or {}).get("inference")
    target = inference.get("target") if isinstance(inference, dict) else None
    trace = target.get("trace") if isinstance(target, dict) else None
    if (
        isinstance(trace, dict)
        and trace.get("backend", "phoenix") == "phoenix"
        and importlib.util.find_spec("phoenix") is None
    ):
        return [
            ConfigValidationIssue(
                code=ConfigValidationCode.DEPENDENCY_MISSING,
                path="/pipeline/inference/target/trace/backend",
                message="Phoenix trace capture requires the otel optional dependency",
            )
        ]
    return []


def _validate_model(
    raw: Any,
    *,
    path: str,
    required_message: str,
    issues: list[ConfigValidationIssue],
) -> None:
    if raw is None:
        issues.append(_semantic_issue(path, required_message))
        return
    try:
        parse_model_config(raw, field_name=path.strip("/").replace("/", "."))
    except ValueError as exc:
        issues.append(_semantic_issue(path, str(exc)))


def _semantic_issue(path: str, message: str) -> ConfigValidationIssue:
    return ConfigValidationIssue(
        code=ConfigValidationCode.SEMANTIC_ERROR,
        path=path,
        message=message,
    )


def _field_name_pointer(field_name: str) -> str:
    if field_name.startswith("pipeline."):
        return "/" + field_name.replace(".", "/")
    if field_name in {
        "artifacts_root",
        "results_dir",
        "suite",
        "run",
        "behavior",
        "context",
    }:
        return f"/{field_name}"
    return ""


def _semantic_error_pointer(message: str) -> str:
    prefixes = (
        ("pipeline.", ""),
        ("default_model", "/default_model"),
        ("behavior.", "/behavior/"),
        ("context", "/context"),
        ("suite", "/suite"),
        ("run", "/run"),
        ("target.", "/pipeline/inference/target/"),
        ("target ", "/pipeline/inference/target"),
        ("trace.", "/pipeline/inference/target/trace/"),
        ("test_set.", "/pipeline/test_set/"),
        ("systematize.", "/pipeline/systematize/"),
        ("inference.", "/pipeline/inference/"),
        ("judge.", "/pipeline/judge/"),
    )
    for prefix, replacement in prefixes:
        if not message.startswith(prefix):
            continue
        token = re.split(r"\s", message, maxsplit=1)[0].rstrip(":")
        if prefix == "pipeline.":
            return "/" + token.replace(".", "/")
        suffix = token[len(prefix):].replace(".", "/")
        return replacement + suffix
    return ""


def _deduplicate_issues(
    issues: list[ConfigValidationIssue],
) -> list[ConfigValidationIssue]:
    result: list[ConfigValidationIssue] = []
    seen: set[tuple[str, str, str]] = set()
    for issue in issues:
        key = (issue.code.value, issue.path, issue.message)
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result


def _invalid_config_error(report: ConfigValidationReport) -> ServiceError:
    return ServiceError(
        ServiceErrorCode.CONFIG_INVALID,
        "Config validation failed",
        details={"validation": report.model_dump(mode="json")},
    )


@contextmanager
def _exclusive_file_lock(path: Path, *, timeout_s: float) -> Iterator[None]:
    deadline = time.monotonic() + timeout_s
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise ServiceError(
                        ServiceErrorCode.CONFLICT,
                        "Timed out waiting for the config write lock",
                    ) from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
