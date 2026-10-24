# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Foundry SDK orchestration with deterministic assets and bounded polling."""

from __future__ import annotations

import json
import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from artifacts import PreparedDataset, write_prepared_dataset
from evaluators import EvaluatorSpec, testing_criteria


class FoundryAdapterError(RuntimeError):
    """Raised for actionable adapter and service failures."""


@dataclass(frozen=True)
class DatasetRef:
    name: str
    version: str
    id: str
    reused: bool


@dataclass(frozen=True)
class EvaluationResult:
    evaluation_id: str
    run_id: str
    status: str
    report_url: str | None


def create_clients(project_endpoint: str) -> tuple[Any, Any, Any]:
    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
    except ModuleNotFoundError as exc:
        raise FoundryAdapterError(
            "Install evaluation/requirements.txt before using cloud mode. "
            "Offline preparation and tests do not require the Foundry SDK."
        ) from exc
    credential = DefaultAzureCredential()
    project_client = AIProjectClient(
        endpoint=project_endpoint,
        credential=credential,
        allow_preview=True,
    )
    return credential, project_client, project_client.get_openai_client()


def prepare_local_asset(
    dataset: PreparedDataset,
    *,
    output_dir: Path,
    dataset_name: str,
    lineage: dict[str, Any],
) -> tuple[Path, Path]:
    safe_name = _safe_name(dataset_name)
    dataset_path = output_dir / f"{safe_name}-{dataset.content_sha256[:16]}.jsonl"
    lineage_path = output_dir / f"{safe_name}-{dataset.content_sha256[:16]}.lineage.json"
    write_prepared_dataset(dataset, dataset_path)
    lineage_payload = {
        **lineage,
        "dataset_name": safe_name,
        "dataset_version": dataset_version(dataset.content_sha256),
        "content_sha256": dataset.content_sha256,
        "row_count": len(dataset.rows),
    }
    lineage_path.write_text(
        json.dumps(lineage_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return dataset_path, lineage_path


def ensure_dataset(
    project_client: Any,
    *,
    name: str,
    dataset: PreparedDataset,
    local_path: Path,
) -> DatasetRef:
    safe_name = _safe_name(name)
    version = dataset_version(dataset.content_sha256)
    try:
        existing = project_client.datasets.get(name=safe_name, version=version)
    except Exception as exc:  # noqa: BLE001 - SDK exception type is optional
        if not _is_not_found(exc):
            raise _service_error("look up the Foundry dataset", exc) from exc
    else:
        _verify_dataset_identity(existing, safe_name, version)
        tags = _field(existing, "tags") or {}
        recorded_hash = tags.get("assert_content_sha256") if isinstance(tags, dict) else None
        if recorded_hash and recorded_hash != dataset.content_sha256:
            raise FoundryAdapterError(
                f"Dataset {safe_name!r} version {version!r} has a conflicting content hash"
            )
        return DatasetRef(
            name=safe_name,
            version=version,
            id=str(_field(existing, "id") or ""),
            reused=True,
        )

    try:
        created = project_client.datasets.upload_file(
            name=safe_name,
            version=version,
            file_path=str(local_path),
        )
    except Exception as exc:  # noqa: BLE001
        raise _service_error("upload the Foundry dataset", exc) from exc
    _verify_dataset_identity(created, safe_name, version)
    dataset_id = str(_field(created, "id") or "")
    if not dataset_id:
        raise FoundryAdapterError("Foundry returned a dataset without an asset ID")
    return DatasetRef(name=safe_name, version=version, id=dataset_id, reused=False)


def dataset_version(content_sha256: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", content_sha256):
        raise FoundryAdapterError("Dataset content hash must be a lowercase SHA-256 hex digest")
    return f"sha256-{content_sha256[:43]}"


def ensure_evaluators(
    project_client: Any,
    specs: list[EvaluatorSpec],
) -> list[Any]:
    versions: list[Any] = []
    for spec in specs:
        try:
            existing = list(project_client.beta.evaluators.list_versions(name=spec.name))
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc):
                existing = []
            else:
                raise _service_error(
                    f"list Foundry evaluator versions for {spec.name!r}", exc
                ) from exc
        matching = [
            item
            for item in existing
            if _field(item, "name") == spec.name
            and (_field(item, "metadata") or {}).get("assert_contract_sha256")
            == spec.fingerprint
        ]
        if matching:
            versions.append(sorted(matching, key=lambda item: str(_field(item, "version")))[-1])
            continue
        try:
            created = project_client.beta.evaluators.create_version(
                name=spec.name,
                evaluator_version=spec.payload,
            )
        except Exception as exc:  # noqa: BLE001
            raise _service_error(f"create evaluator version {spec.name!r}", exc) from exc
        authoritative_version = _field(created, "version")
        if not authoritative_version:
            raise FoundryAdapterError(
                f"Foundry returned no authoritative version for evaluator {spec.name!r}"
            )
        returned_hash = (_field(created, "metadata") or {}).get("assert_contract_sha256")
        if returned_hash and returned_hash != spec.fingerprint:
            raise FoundryAdapterError(
                f"Foundry returned conflicting metadata for evaluator {spec.name!r}"
            )
        versions.append(created)
    return versions


def dataset_schema(*, include_sample_schema: bool = False) -> dict[str, Any]:
    return {
        "type": "custom",
        "item_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "response": {"type": "string"},
                "tool_evidence": {},
                "assert_test_case_id": {"type": "string"},
                "assert_test_case_type": {"type": "string"},
                "assert_behavior": {"type": "string"},
                "assert_dimensions": {"type": "object"},
                "assert_suite": {"type": "string"},
                "assert_run": {"type": "string"},
                "assert_test_case_content_sha256": {"type": "string"},
            },
            "required": ["query"],
        },
        "include_sample_schema": include_sample_schema,
    }


def conversation_schema() -> dict[str, Any]:
    return {
        "type": "custom",
        "item_schema": {
            "type": "object",
            "properties": {"messages": {"type": "array"}},
            "required": ["messages"],
        },
        "include_sample_schema": False,
    }


def trace_schema() -> dict[str, str]:
    return {"type": "azure_ai_source", "scenario": "traces"}


def create_evaluation(
    openai_client: Any,
    *,
    name: str,
    data_source_config: dict[str, Any],
    criteria: list[dict[str, Any]],
    metadata: dict[str, str],
) -> Any:
    try:
        return openai_client.evals.create(
            name=name,
            data_source_config=data_source_config,
            testing_criteria=criteria,
            metadata=metadata,
        )
    except Exception as exc:  # noqa: BLE001
        raise _service_error("create a fresh Foundry evaluation definition", exc) from exc


def run_dataset_evaluation(
    openai_client: Any,
    *,
    eval_object: Any,
    dataset: DatasetRef,
    name: str,
    metadata: dict[str, str],
    timeout_seconds: float,
    poll_seconds: float,
) -> EvaluationResult:
    data_source = dataset_run_data_source(dataset)
    return _create_and_poll(
        openai_client,
        eval_object=eval_object,
        name=name,
        data_source=data_source,
        metadata=metadata,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )


def dataset_run_data_source(dataset: DatasetRef) -> dict[str, Any]:
    return {
        "type": "jsonl",
        "source": {"type": "file_id", "id": dataset.id},
    }


def run_agent_evaluation(
    openai_client: Any,
    *,
    eval_object: Any,
    dataset: DatasetRef,
    agent_name: str,
    agent_version: str | None,
    protocol: str,
    name: str,
    metadata: dict[str, str],
    timeout_seconds: float,
    poll_seconds: float,
) -> EvaluationResult:
    data_source = agent_run_data_source(
        dataset,
        agent_name=agent_name,
        agent_version=agent_version,
        protocol=protocol,
    )
    return _create_and_poll(
        openai_client,
        eval_object=eval_object,
        name=name,
        data_source=data_source,
        metadata=metadata,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )


def agent_run_data_source(
    dataset: DatasetRef,
    *,
    agent_name: str,
    agent_version: str | None,
    protocol: str,
) -> dict[str, Any]:
    target: dict[str, Any] = {"type": "azure_ai_agent", "name": agent_name}
    if agent_version:
        target["version"] = agent_version
    if protocol == "responses":
        input_messages: dict[str, Any] = {
            "type": "template",
            "template": [
                {
                    "type": "message",
                    "role": "user",
                    "content": {"type": "input_text", "text": "{{item.query}}"},
                }
            ],
        }
    elif protocol == "invocations":
        input_messages = {"message": "{{item.query}}"}
    else:
        raise FoundryAdapterError("Agent protocol must be 'responses' or 'invocations'")
    data_source = {
        "type": "azure_ai_target_completions",
        "source": {"type": "file_id", "id": dataset.id},
        "input_messages": input_messages,
        "target": target,
    }
    return data_source


def run_scenario_simulation(
    openai_client: Any,
    *,
    eval_object: Any,
    dataset: DatasetRef,
    agent_name: str,
    agent_version: str | None,
    simulator_model: str,
    max_turns: int,
    name: str,
    metadata: dict[str, str],
    timeout_seconds: float,
    poll_seconds: float,
) -> EvaluationResult:
    data_source = scenario_run_data_source(
        dataset,
        agent_name=agent_name,
        agent_version=agent_version,
        simulator_model=simulator_model,
        max_turns=max_turns,
    )
    return _create_and_poll(
        openai_client,
        eval_object=eval_object,
        name=name,
        data_source=data_source,
        metadata=metadata,
        extra_body={"evaluation_level": "conversation"},
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )


def scenario_run_data_source(
    dataset: DatasetRef,
    *,
    agent_name: str,
    agent_version: str | None,
    simulator_model: str,
    max_turns: int,
) -> dict[str, Any]:
    target: dict[str, Any] = {"type": "azure_ai_agent", "name": agent_name}
    if agent_version:
        target["version"] = agent_version
    data_source = {
        "type": "azure_ai_target_completions",
        "source": {"type": "file_id", "id": dataset.id},
        "target": target,
        "item_generation_params": {
            "type": "conversation_gen_preview",
            "model": simulator_model,
            "num_conversations": 1,
            "max_turns": max_turns,
            "data_mapping": {
                "test_case_description": "test_case_description",
                "id": "id",
            },
        },
    }
    return data_source


def run_trace_evaluation(
    openai_client: Any,
    *,
    eval_object: Any,
    level: str,
    trace_source: dict[str, Any],
    name: str,
    metadata: dict[str, str],
    timeout_seconds: float,
    poll_seconds: float,
) -> EvaluationResult:
    data_source = trace_run_data_source(level=level, trace_source=trace_source)
    return _create_and_poll(
        openai_client,
        eval_object=eval_object,
        name=name,
        data_source=data_source,
        metadata=metadata,
        extra_body={"evaluation_level": level},
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )


def trace_run_data_source(
    *,
    level: str,
    trace_source: dict[str, Any],
) -> dict[str, Any]:
    source_type = trace_source.get("type")
    if level == "turn" and source_type == "turn_trace_ids":
        data_source = {
            "type": "azure_ai_traces",
            "trace_ids": trace_source["trace_ids"],
            "lookback_hours": trace_source["lookback_hours"],
        }
    elif level == "turn" and source_type == "turn_agent_filter":
        data_source = {
            "type": "azure_ai_traces",
            "agent_id": trace_source["agent_id"],
            "max_traces": trace_source["max_traces"],
            "lookback_hours": trace_source["lookback_hours"],
        }
    else:
        data_source = {
            "type": "azure_ai_trace_data_source_preview",
            "trace_source": trace_source,
        }
    return data_source


def _create_and_poll(
    openai_client: Any,
    *,
    eval_object: Any,
    name: str,
    data_source: dict[str, Any],
    metadata: dict[str, str],
    timeout_seconds: float,
    poll_seconds: float,
    extra_body: dict[str, Any] | None = None,
) -> EvaluationResult:
    eval_id = str(_field(eval_object, "id") or "")
    if not eval_id:
        raise FoundryAdapterError("Foundry returned an evaluation without an ID")
    kwargs: dict[str, Any] = {
        "eval_id": eval_id,
        "name": name,
        "data_source": data_source,
        "metadata": metadata,
    }
    if extra_body:
        kwargs["extra_body"] = extra_body
    try:
        run = openai_client.evals.runs.create(**kwargs)
    except Exception as exc:  # noqa: BLE001
        raise _service_error("create the Foundry evaluation run", exc) from exc
    return poll_run(
        openai_client,
        eval_id=eval_id,
        run_id=str(_field(run, "id") or ""),
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )


def poll_run(
    openai_client: Any,
    *,
    eval_id: str,
    run_id: str,
    timeout_seconds: float = 1800,
    poll_seconds: float = 5,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> EvaluationResult:
    if not run_id:
        raise FoundryAdapterError("Foundry returned an evaluation run without an ID")
    deadline = clock() + timeout_seconds
    while True:
        try:
            run = openai_client.evals.runs.retrieve(run_id=run_id, eval_id=eval_id)
        except Exception as exc:  # noqa: BLE001
            raise _service_error("retrieve the Foundry evaluation run", exc) from exc
        status = str(_field(run, "status") or "").lower()
        if status == "completed":
            return EvaluationResult(
                evaluation_id=eval_id,
                run_id=run_id,
                status=status,
                report_url=_field(run, "report_url"),
            )
        if status in {"failed", "canceled", "cancelled"}:
            error = _safe_error_text(_field(run, "error"))
            raise FoundryAdapterError(
                f"Foundry evaluation run {run_id} ended in {status}"
                + (f": {error}" if error else "")
            )
        if clock() >= deadline:
            raise FoundryAdapterError(
                f"Timed out after {timeout_seconds:g}s waiting for Foundry evaluation run {run_id}"
            )
        sleep(poll_seconds)


def turn_criteria(
    evaluator_versions: list[Any],
    specs: list[EvaluatorSpec],
    *,
    model_deployment: str,
    target_generated: bool = False,
    trace: bool = False,
) -> list[dict[str, Any]]:
    criteria = testing_criteria(
        evaluator_versions,
        specs,
        model_deployment=model_deployment,
    )
    for criterion in criteria:
        if target_generated:
            criterion["data_mapping"] = {
                "query": "{{item.query}}",
                "response": "{{sample.output_text}}",
                "tool_evidence": "{{sample.output_items}}",
            }
        elif trace:
            criterion["data_mapping"] = {
                "query": "{{item.query}}",
                "response": "{{item.response}}",
                "tool_evidence": "{{item.tool_calls}}",
            }
    return criteria


def metadata(
    *,
    suite: str,
    run: str,
    dataset_hash: str | None,
    route: str,
    criteria_hash: str,
) -> dict[str, str]:
    values = {
        "assert_suite": suite,
        "assert_run": run,
        "assert_route": route,
        "assert_adapter": "foundry-custom-evaluation-v1",
        "assert_criteria_sha256": criteria_hash,
    }
    if dataset_hash:
        values["assert_dataset_sha256"] = dataset_hash
    return values


def dataset_lineage_metadata(dataset: PreparedDataset) -> dict[str, str]:
    case_ids = sorted(str(row["assert_test_case_id"]) for row in dataset.rows)
    dimension_names = sorted(
        {
            str(name)
            for row in dataset.rows
            for name in (row.get("assert_dimensions") or {})
        }
    )
    return {
        "assert_row_count": str(len(dataset.rows)),
        "assert_test_case_ids_sha256": hashlib.sha256(
            "\n".join(case_ids).encode("utf-8")
        ).hexdigest(),
        "assert_dimension_keys": ",".join(dimension_names)[:500],
    }


def criteria_hash(specs: Iterable[EvaluatorSpec]) -> str:
    joined = "\n".join(spec.fingerprint for spec in specs)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-._")
    if not safe:
        raise FoundryAdapterError("Foundry asset name must contain an alphanumeric character")
    return safe[:120]


def _verify_dataset_identity(value: Any, name: str, version: str) -> None:
    actual_name = str(_field(value, "name") or "")
    actual_version = str(_field(value, "version") or "")
    if actual_name != name or actual_version != version:
        raise FoundryAdapterError(
            "Foundry dataset name/version collision: "
            f"requested {name!r}/{version!r}, received {actual_name!r}/{actual_version!r}"
        )


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _is_not_found(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    return status_code == 404 or "notfound" in type(exc).__name__.lower()


def _safe_error_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"https?://[^\s\"']+", "[endpoint removed]", text)
    text = re.sub(r"(?i)\b(bearer|api[_-]?key|token|secret)\s+[^,\s]+", r"\1 [removed]", text)
    return text[:1000]


def _service_error(action: str, exc: Exception) -> FoundryAdapterError:
    status = getattr(exc, "status_code", None)
    detail = _safe_error_text(exc)
    prefix = f"Unable to {action}"
    if status:
        prefix += f" (HTTP {status})"
    return FoundryAdapterError(f"{prefix}: {detail}")
