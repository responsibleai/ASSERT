# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Strict, deterministic ASSERT artifact preparation for Foundry evaluation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from assert_ai.core.io import row_behavior
from assert_ai.core.transcript import _transcript_from_dict


class ArtifactError(ValueError):
    """Raised when ASSERT artifacts cannot be converted without ambiguity."""


@dataclass(frozen=True)
class PreparedDataset:
    rows: tuple[dict[str, Any], ...]
    payload: bytes
    content_sha256: str


_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_QUOTED_WINDOWS_PATH_RE = re.compile(
    r"""(?P<quote>["'])(?:[a-z]:[\\/]|\\\\)[^"']+(?P=quote)""",
    re.IGNORECASE,
)
_WINDOWS_PATH_RE = re.compile(r"(?i)(?<![\w])(?:[a-z]:[\\/]|\\\\)[^\s\"'<>|]+")
_QUOTED_POSIX_PATH_RE = re.compile(
    r"""(?P<quote>["'])/(?!/)(?:[^/"']+/)+[^"']+(?P=quote)"""
)
_POSIX_PATH_RE = re.compile(
    r"(?<![\w:/])/(?!/)(?:[A-Za-z0-9._~-]+/)+[A-Za-z0-9._~+-]+"
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(?<![?&])\b([A-Z0-9_.-]*(?:API[_-]?KEY|ACCESS[_-]?TOKEN|REFRESH[_-]?TOKEN|"
    r"ID[_-]?TOKEN|AUTH[_-]?TOKEN|CLIENT[_-]?SECRET|PASSWORD|PASSWD|SECRET))"
    r"\b\s*=\s*([^\s,;]+)"
)
_STRUCTURED_SECRET_RE = re.compile(
    r"""(?ix)
    (?P<prefix>
        (?:
            "(?:api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|
               auth[_-]?token|client[_-]?secret|password|passwd|secret|
               authorization|credential|connection[_-]?string|sas[_-]?token)"
            |
            '(?:api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|
               auth[_-]?token|client[_-]?secret|password|passwd|secret|
               authorization|credential|connection[_-]?string|sas[_-]?token)'
            |
            (?:api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|
               auth[_-]?token|client[_-]?secret|password|passwd|secret|
               authorization|credential|connection[_-]?string|sas[_-]?token)
        )
        \s*:\s*
    )
    (?P<value>
        "(?:\\.|[^"\\])*"
        |
        '(?:\\.|[^'\\])*'
        |
        [^\s,}\]]+
    )
    """
)
_SENSITIVE_KEY_SUFFIXES = (
    "apikey",
    "accesstoken",
    "refreshtoken",
    "idtoken",
    "authtoken",
    "clientsecret",
    "password",
    "passwd",
    "secret",
    "credential",
    "connectionstring",
    "sastoken",
)
_URL_SECRET_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "code",
    "credential",
    "key",
    "password",
    "refresh_token",
    "secret",
    "sig",
    "signature",
    "token",
}
_SOURCE_HASH_FIELDS = (
    "source_test_case_content_sha256",
    "source_test_case_sha256",
    "source_case_content_sha256",
    "source_case_sha256",
    "test_case_content_sha256",
    "test_case_sha256",
)
_SOURCE_ID_FIELDS = ("source_test_case_id", "source_case_id")
_SOURCE_OBJECT_FIELDS = ("source_test_case", "source_case")


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def rows_to_jsonl_bytes(rows: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(row) for row in rows)


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_jsonl(path: Path, *, required: bool = True) -> list[dict[str, Any]]:
    if not path.is_file():
        if required:
            raise ArtifactError(f"Required ASSERT artifact not found: {path.name}")
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ArtifactError(f"{path.name}:{line_number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(row, dict):
                raise ArtifactError(f"{path.name}:{line_number}: each row must be a JSON object")
            rows.append(row)
    if required and not rows:
        raise ArtifactError(f"{path.name} contains no rows")
    return rows


def _index_unique(rows: Iterable[dict[str, Any]], *, source: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows, 1):
        raw_id = row.get("test_case_id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            raise ArtifactError(f"{source} row {index} has no non-empty test_case_id")
        case_id = raw_id.strip()
        if case_id in indexed:
            raise ArtifactError(f"{source} contains duplicate test_case_id {case_id!r}")
        indexed[case_id] = row
    return indexed


def _seed(row: dict[str, Any], *, source: str) -> dict[str, Any]:
    seed = row.get("seed")
    if not isinstance(seed, dict):
        raise ArtifactError(f"{source} test case {row.get('test_case_id')!r} has no seed object")
    return seed


def _dimensions(row: dict[str, Any]) -> dict[str, str]:
    raw = row.get("dimensions")
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def _behavior(row: dict[str, Any]) -> str:
    return row_behavior(row) or str(row.get("behavior") or "")


def _lineage(row: dict[str, Any], *, suite: str, run: str) -> dict[str, Any]:
    case_id = str(row["test_case_id"])
    return {
        "assert_test_case_id": case_id,
        "assert_test_case_type": str(row["type"]),
        "assert_behavior": _behavior(row),
        "assert_dimensions": _dimensions(row),
        "assert_suite": suite,
        "assert_run": run,
        "assert_test_case_content_sha256": sha256_hex(canonical_json_bytes(row)),
    }


def _prompt_row(row: dict[str, Any], *, suite: str, run: str) -> dict[str, Any]:
    seed = _seed(row, source="test_set.jsonl")
    query = str(seed.get("description") or "").strip()
    if not query:
        raise ArtifactError(f"Prompt test case {row['test_case_id']!r} has an empty description")
    result = _lineage(row, suite=suite, run=run)
    result["query"] = query
    return result


def _scenario_row(row: dict[str, Any], *, suite: str, run: str) -> dict[str, Any]:
    seed = _seed(row, source="test_set.jsonl")
    description = str(seed.get("description") or "").strip()
    if not description:
        raise ArtifactError(f"Scenario test case {row['test_case_id']!r} has an empty description")
    result = _lineage(row, suite=suite, run=run)
    result.update(
        {
            "id": str(row["test_case_id"]),
            "test_case_description": description,
        }
    )
    desired_turns = seed.get("desired_num_turns")
    if isinstance(desired_turns, int) and desired_turns > 0:
        result["desired_num_turns"] = desired_turns
    return result


def prepare_native_rows(
    test_set_path: Path,
    *,
    suite: str,
    run: str,
) -> tuple[PreparedDataset | None, PreparedDataset | None]:
    rows = _strict_jsonl(test_set_path)
    _index_unique(rows, source="test_set.jsonl")
    prompt_rows: list[dict[str, Any]] = []
    scenario_rows: list[dict[str, Any]] = []
    for row in rows:
        kind = row.get("type")
        if kind == "prompt":
            prompt_rows.append(_prompt_row(row, suite=suite, run=run))
        elif kind == "scenario":
            scenario_rows.append(_scenario_row(row, suite=suite, run=run))
        else:
            raise ArtifactError(
                f"test_set.jsonl test case {row.get('test_case_id')!r} has unsupported type {kind!r}"
            )
    return _prepared(prompt_rows), _prepared(scenario_rows)


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _is_sensitive_key(value: Any) -> bool:
    normalized = _normalized_key(value)
    return normalized == "authorization" or normalized.endswith(_SENSITIVE_KEY_SUFFIXES)


def _sanitize_url_match(match: re.Match[str]) -> str:
    raw = match.group(0)
    trailing = ""
    while raw and raw[-1] in ".,;)]}":
        trailing = raw[-1] + trailing
        raw = raw[:-1]
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        netloc = hostname
        if parsed.port is not None:
            netloc += f":{parsed.port}"

        query = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            replacement = "[credential removed]" if key.lower() in _URL_SECRET_KEYS else value
            query.append((key, replacement))

        segments = parsed.path.split("/")
        redact_next = False
        for index, segment in enumerate(segments):
            normalized = _normalized_key(segment)
            if redact_next or _looks_like_token(segment):
                segments[index] = "[credential removed]"
                redact_next = False
            elif normalized in {
                "apikey",
                "accesstoken",
                "authtoken",
                "credential",
                "key",
                "secret",
                "token",
            }:
                redact_next = True

        fragment = parsed.fragment
        if fragment:
            fragment_pairs = parse_qsl(fragment, keep_blank_values=True)
            if fragment_pairs:
                fragment = urlencode(
                    [
                        (
                            key,
                            "[credential removed]"
                            if key.lower() in _URL_SECRET_KEYS
                            else value,
                        )
                        for key, value in fragment_pairs
                    ]
                )
            elif _looks_like_token(fragment):
                fragment = "[credential removed]"

        sanitized = urlunsplit(
            (
                parsed.scheme,
                netloc,
                "/".join(segments),
                urlencode(query),
                fragment,
            )
        )
        return sanitized + trailing
    except (TypeError, ValueError):
        return "[credential-bearing URL removed]" + trailing


def _looks_like_token(value: str) -> bool:
    return len(value) >= 24 and (
        value.count(".") == 2
        or bool(re.fullmatch(r"[A-Za-z0-9_-]{24,}={0,2}", value))
    )


def _sanitize_text(value: str) -> str:
    value = _URL_RE.sub(_sanitize_url_match, value)
    value = _STRUCTURED_SECRET_RE.sub(
        lambda match: f'{match.group("prefix")}"[credential removed]"',
        value,
    )
    value = _SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}=[credential removed]",
        value,
    )
    value = _QUOTED_WINDOWS_PATH_RE.sub(
        lambda match: f'{match.group("quote")}[local path removed]{match.group("quote")}',
        value,
    )
    value = _WINDOWS_PATH_RE.sub("[local path removed]", value)
    value = _QUOTED_POSIX_PATH_RE.sub(
        lambda match: f'{match.group("quote")}[local path removed]{match.group("quote")}',
        value,
    )
    value = _POSIX_PATH_RE.sub("[local path removed]", value)
    return value


def _sanitize_structure(value: Any, *, key: Any = None) -> Any:
    if key is not None and _is_sensitive_key(key):
        return "[credential removed]"
    if isinstance(value, Mapping):
        return {
            str(item_key): _sanitize_structure(item_value, key=item_key)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_structure(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_structure(item) for item in value)
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _target_view(inference_row: dict[str, Any]) -> tuple[list[dict[str, str]], str]:
    sanitized_row = _sanitize_structure(inference_row)
    if not isinstance(sanitized_row, dict):
        raise ArtifactError("Inference row sanitization produced an invalid structure")
    transcript = _transcript_from_dict(sanitized_row)
    messages: list[dict[str, str]] = []
    for entry in transcript.collect_searchable_messages_with_ids("target"):
        content = _sanitize_text(entry.message.content)
        if not content.strip():
            continue
        messages.append({"role": entry.message.role, "content": content})
    rendered = _sanitize_text(
        transcript.format_transcript(
            "target",
            skip_system=False,
            numbered=True,
            number_system=True,
        )
    )
    return messages, rendered


def _query_from_test_case(test_row: dict[str, Any], messages: list[dict[str, str]]) -> str:
    seed = _seed(test_row, source="test_set.jsonl")
    if test_row.get("type") == "prompt":
        return str(seed.get("description") or "").strip()
    user_messages = [message["content"] for message in messages if message["role"] == "user"]
    return "\n\n".join(user_messages) or str(seed.get("description") or "").strip()


def _test_case_hash(row: dict[str, Any]) -> str:
    for field in _SOURCE_HASH_FIELDS:
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return sha256_hex(canonical_json_bytes(row))


def _validate_source_object(
    test_row: dict[str, Any],
    source_row: Mapping[str, Any],
    *,
    case_id: str,
    field: str,
) -> None:
    for identity_field in ("test_case_id", "type", "seed"):
        if identity_field in source_row and source_row[identity_field] != test_row.get(identity_field):
            raise ArtifactError(
                f"Inference test case {case_id!r} {field}.{identity_field} does not match "
                "test_set.jsonl"
            )
    if "behavior" in source_row and _behavior(dict(source_row)) != _behavior(test_row):
        raise ArtifactError(
            f"Inference test case {case_id!r} {field}.behavior does not match test_set.jsonl"
        )
    if "dimensions" in source_row and _dimensions(dict(source_row)) != _dimensions(test_row):
        raise ArtifactError(
            f"Inference test case {case_id!r} {field}.dimensions does not match test_set.jsonl"
        )


def _validate_join_identity(
    test_row: dict[str, Any],
    inference_row: dict[str, Any],
    *,
    case_id: str,
) -> None:
    test_type = test_row.get("type")
    inference_type = inference_row.get("type")
    if inference_type != test_type:
        raise ArtifactError(
            f"Inference test case {case_id!r} type {inference_type!r} does not match "
            f"test_set.jsonl type {test_type!r}"
        )

    test_behavior = _behavior(test_row)
    inference_behavior = _behavior(inference_row)
    if inference_behavior != test_behavior:
        raise ArtifactError(
            f"Inference test case {case_id!r} behavior {inference_behavior!r} does not match "
            f"test_set.jsonl behavior {test_behavior!r}"
        )

    test_dimensions = _dimensions(test_row)
    inference_dimensions = _dimensions(inference_row)
    if inference_dimensions != test_dimensions:
        raise ArtifactError(
            f"Inference test case {case_id!r} dimensions do not match test_set.jsonl"
        )

    for field in _SOURCE_ID_FIELDS:
        source_id = inference_row.get(field)
        if source_id is not None and str(source_id) != case_id:
            raise ArtifactError(
                f"Inference test case {case_id!r} {field} {source_id!r} does not match "
                "test_set.jsonl"
            )

    if "seed" in inference_row and inference_row["seed"] != test_row.get("seed"):
        raise ArtifactError(
            f"Inference test case {case_id!r} source seed does not match test_set.jsonl"
        )

    for field in _SOURCE_OBJECT_FIELDS:
        source_row = inference_row.get(field)
        if source_row is not None:
            if not isinstance(source_row, Mapping):
                raise ArtifactError(
                    f"Inference test case {case_id!r} {field} must be an object"
                )
            _validate_source_object(test_row, source_row, case_id=case_id, field=field)

    expected_hash = _test_case_hash(test_row)
    for field in _SOURCE_HASH_FIELDS:
        source_hash = inference_row.get(field)
        if source_hash is not None and str(source_hash).strip().lower() != expected_hash:
            raise ArtifactError(
                f"Inference test case {case_id!r} {field} does not match test_set.jsonl"
            )


def prepare_precomputed_rows(
    test_set_path: Path,
    inference_set_path: Path,
    *,
    suite: str,
    run: str,
) -> PreparedDataset:
    test_rows = _strict_jsonl(test_set_path)
    inference_rows = _strict_jsonl(inference_set_path)
    tests = _index_unique(test_rows, source="test_set.jsonl")
    inferences = _index_unique(inference_rows, source="inference_set.jsonl")

    missing = sorted(set(tests).difference(inferences))
    extra = sorted(set(inferences).difference(tests))
    if missing:
        raise ArtifactError(
            "inference_set.jsonl is missing test_case_id values: " + ", ".join(missing)
        )
    if extra:
        raise ArtifactError(
            "inference_set.jsonl contains unknown test_case_id values: " + ", ".join(extra)
        )

    joined: list[dict[str, Any]] = []
    for case_id in sorted(tests):
        test_row = tests[case_id]
        kind = test_row.get("type")
        if kind not in {"prompt", "scenario"}:
            raise ArtifactError(f"Test case {case_id!r} has unsupported type {kind!r}")
        inference_row = inferences[case_id]
        _validate_join_identity(test_row, inference_row, case_id=case_id)
        messages, response = _target_view(inference_row)
        assistant_or_tool = [
            message for message in messages if message["role"] in {"assistant", "tool"}
        ]
        if not assistant_or_tool:
            raise ArtifactError(f"Inference test case {case_id!r} has no target output or tool evidence")
        if not response.strip():
            raise ArtifactError(f"Inference test case {case_id!r} rendered an empty transcript")
        row = _lineage(test_row, suite=suite, run=run)
        row.update(
            {
                "query": _query_from_test_case(test_row, messages),
                "response": response,
                "messages": messages,
                "tool_evidence": "\n\n".join(
                    message["content"] for message in messages if message["role"] == "tool"
                ),
            }
        )
        joined.append(row)
    return _prepared(joined)


def _prepared(rows: list[dict[str, Any]]) -> PreparedDataset | None:
    if not rows:
        return None
    ordered = tuple(sorted(rows, key=lambda row: str(row["assert_test_case_id"])))
    payload = rows_to_jsonl_bytes(ordered)
    return PreparedDataset(rows=ordered, payload=payload, content_sha256=sha256_hex(payload))


def write_prepared_dataset(dataset: PreparedDataset, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_parent = output_path.parent.resolve()
    resolved_output = output_path.resolve()
    try:
        resolved_output.relative_to(resolved_parent)
    except ValueError as exc:
        raise ArtifactError("Output path escapes its output directory") from exc
    output_path.write_bytes(dataset.payload)
    return output_path
