# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Execute PyRIT attacks against an ASSERT target and write native findings."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from assert_ai.config import resolve_stage_paths
from assert_ai.core.config_model import (
    DEFAULT_INFERENCE_MAX_TOKENS,
    EvaluationConfig,
    TargetConfig,
)
from assert_ai.core.io import (
    INFERENCE_SET_FILE,
    SCORES_FILE,
    append_jsonl_row,
    load_json,
    load_jsonl,
    write_json,
    write_jsonl,
)
from assert_ai.core.model_client import LLMAuthError, LLMInputError, Message
from assert_ai.core.red_team import (
    FINDING_SCHEMA_VERSION,
    PYRIT_VERSION,
    AttackDefinition,
    AttackPlan,
    OutboundSink,
    attack_dimensions,
    build_score_row,
    build_taxonomy,
    build_test_set,
    load_attack_plan,
)
from assert_ai.core.transcript import (
    AddMessageEdit,
    Message as TranscriptMessage,
    Transcript,
    TranscriptEvent,
    TranscriptMetadata,
)
from assert_ai.stages.inference import (
    _append_llm_calls,
    _record_interaction_messages,
    _record_runtime_metadata,
    _record_system_message,
    build_target_session,
)
from assert_ai.viewer_read_model import build_run_viewer_artifacts

log = logging.getLogger(__name__)

SCOPE = "run"
SUITE_OUTPUT = None

_CONFIG_HASH_FILE = ".red_team_config_hash"


@dataclass
class TargetObservation:
    transcript: Transcript
    runtime_mode: str
    error: str | None = None
    tool_evidence_available: bool = True


@dataclass
class ExecutedAttack:
    attack: AttackDefinition
    result: Any | None
    observation: TargetObservation | None
    error: str | None = None
    retryable: bool = False


def _load_pyrit() -> SimpleNamespace:
    try:
        from pyrit.executor.attack import (
            AttackExecutor,
            AttackScoringConfig,
            PromptSendingAttack,
        )
        from pyrit.models import construct_response_from_request
        from pyrit.prompt_target import PromptTarget
        from pyrit.score import SubStringScorer
        from pyrit.setup import IN_MEMORY, initialize_pyrit_async
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "The red_team stage requires PyRIT. Install it with "
            'python -m pip install "assert-ai[redteam]".'
        ) from exc
    return SimpleNamespace(
        AttackExecutor=AttackExecutor,
        AttackScoringConfig=AttackScoringConfig,
        PromptSendingAttack=PromptSendingAttack,
        PromptTarget=PromptTarget,
        SubStringScorer=SubStringScorer,
        construct_response_from_request=construct_response_from_request,
        initialize_pyrit_async=initialize_pyrit_async,
        IN_MEMORY=IN_MEMORY,
    )


def _target_identifier(target: TargetConfig) -> str:
    if target.model:
        return str(target.model.name)
    return str(target.callable or target.endpoint or target.connector or "")


def _config_fingerprint(
    *,
    attacks_path: Path,
    target: TargetConfig,
    evaluation: EvaluationConfig,
) -> str:
    runtime = asdict(evaluation.inference)
    runtime["concurrency"] = 1
    payload = {
        "attacks_sha256": hashlib.sha256(attacks_path.read_bytes()).hexdigest(),
        "target": asdict(target),
        "runtime": runtime,
        "pyrit_version": PYRIT_VERSION,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


def _validate_evidence_capability(
    *,
    plan: AttackPlan,
    target: TargetConfig,
) -> None:
    if (
        target.tools is not None
        and target.tools.simulator is not None
        and target.tools.toolset is None
    ):
        raise ValueError(
            "red_team does not support simulator-only target.tools because attack "
            "definitions do not provide per-test tool schemas. Configure a fixed "
            "toolset or use a tool module."
        )
    if not plan.outbound_sinks:
        return
    if target.tools is not None and target.tools.simulator is not None:
        raise ValueError(
            "red_team attack data declares outbound_sinks, but simulated tool "
            "results cannot prove an outbound action. Use a traced callable or "
            "a hosted model with a real tool module."
        )
    if target.is_endpoint:
        raise ValueError(
            "red_team attack data declares outbound_sinks, but endpoint targets "
            "do not expose tool-call evidence. Use a traced callable, a hosted "
            "model with a real tool module."
        )
    if target.is_external:
        raise ValueError(
            "red_team attack data declares outbound_sinks, but connector targets "
            "do not declare whether they return tool-call evidence. Use a traced "
            "callable or a hosted model with target.tools."
        )
    if target.is_callable and target.trace is None:
        raise ValueError(
            "red_team attack data declares outbound_sinks, but callable targets "
            "require target.trace to expose tool-call evidence."
        )
    if target.model is not None and target.tools is None:
        raise ValueError(
            "red_team attack data declares outbound_sinks, but this hosted target "
            "does not declare target.tools. Use a traced callable, a hosted model "
            "with a real tool module."
        )


def _write_stable_suite_inputs(*, suite_root: Path, plan: AttackPlan) -> None:
    taxonomy = build_taxonomy(plan)
    test_set = build_test_set(plan)
    taxonomy_path = suite_root / "taxonomy.json"
    test_set_path = suite_root / "test_set.jsonl"

    existing_taxonomy = load_json(taxonomy_path)
    if existing_taxonomy is not None and existing_taxonomy != taxonomy:
        raise ValueError(
            "Red-team taxonomy changed for an existing suite. Use a new suite id "
            "so prior run findings keep their original taxonomy."
        )
    existing_test_set = load_jsonl(test_set_path)
    if existing_test_set and existing_test_set != test_set:
        raise ValueError(
            "Red-team attacks changed for an existing suite. Use a new suite id "
            "so prior run findings keep their original attack definitions."
        )

    if existing_taxonomy is None:
        write_json(taxonomy_path, taxonomy)
    if not existing_test_set:
        write_jsonl(test_set_path, test_set)


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw) if raw is not None else ""


def _pyrit_score_value(result: Any | None) -> bool | None:
    score = getattr(result, "last_score", None)
    if score is None:
        return None
    value = score.get_value()
    return value if isinstance(value, bool) else None


def _contains_text(value: Any, needle: str) -> bool:
    if isinstance(value, str):
        return needle in value.casefold()
    if isinstance(value, dict):
        return any(
            _contains_text(key, needle) or _contains_text(item, needle)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_contains_text(item, needle) for item in value)
    return needle in str(value).casefold()


def _finding_evidence(
    transcript: Transcript,
    *,
    substring: str,
    outbound_sinks: tuple[OutboundSink, ...],
) -> tuple[bool, str | None, list[int]]:
    """Search target-visible output and outbound tool arguments for a canary."""

    message_index_by_event = {
        int(message_id.removeprefix("event:")): index
        for index, (message_id, _) in enumerate(
            transcript.collect_messages_with_ids("target"),
            start=1,
        )
        if message_id.startswith("event:")
    }
    normalized_substring = substring.casefold()
    for event_index, event in enumerate(transcript.events):
        edit = event.edit.model_dump()
        edit_type = edit.get("type")
        if edit_type == "add_message":
            message = edit.get("message")
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            content = message.get("content")
            if (
                role == "assistant"
                and isinstance(content, str)
                and normalized_substring in content.casefold()
            ):
                turn = message_index_by_event.get(event_index)
                return True, "assistant_response", [turn] if turn else []
        if edit_type == "tool_call":
            tool_name = edit.get("tool_name")
            tool_args = edit.get("tool_args")
            tool_result = edit.get("tool_result")
            if not isinstance(tool_name, str) or not isinstance(tool_args, dict):
                continue
            for sink in outbound_sinks:
                if sink.tool_name != tool_name or sink.argument not in tool_args:
                    continue
                if not _contains_text(
                    tool_args[sink.argument],
                    normalized_substring,
                ):
                    continue
                if sink.result_contains is not None:
                    if (
                        not isinstance(tool_result, str)
                        or sink.result_contains.casefold() not in tool_result.casefold()
                    ):
                        continue
                turn = message_index_by_event.get(event_index)
                return (
                    True,
                    f"tool_argument:{tool_name}.{sink.argument}",
                    [turn] if turn else [],
                )
    return False, None, []


def _sink_evidence_error(
    transcript: Transcript,
    *,
    outbound_sinks: tuple[OutboundSink, ...],
    substring: str,
) -> tuple[str, bool] | None:
    normalized_substring = substring.casefold()
    missing_argument_error: tuple[str, bool] | None = None
    unconfirmed_result_error: tuple[str, bool] | None = None
    for event in transcript.events:
        edit = event.edit.model_dump()
        if edit.get("type") != "tool_call":
            continue
        tool_name = edit.get("tool_name")
        tool_args = edit.get("tool_args")
        if not isinstance(tool_name, str) or not isinstance(tool_args, dict):
            continue
        for sink in outbound_sinks:
            if sink.tool_name == tool_name and sink.argument not in tool_args:
                missing_argument_error = (
                    f"Configured outbound sink {tool_name}.{sink.argument} was "
                    f"observed as tool {tool_name}, but the argument was absent.",
                    True,
                )
            if (
                sink.tool_name == tool_name
                and sink.argument in tool_args
                and _contains_text(tool_args[sink.argument], normalized_substring)
                and sink.result_contains is not None
                and (
                    not isinstance(edit.get("tool_result"), str)
                    or sink.result_contains.casefold()
                    not in str(edit.get("tool_result")).casefold()
                )
            ):
                unconfirmed_result_error = (
                    f"Outbound sink {tool_name}.{sink.argument} contained the "
                    "scored value, but the tool result did not match the "
                    "configured success marker.",
                    False,
                )
    return unconfirmed_result_error or missing_argument_error


def _red_team_metadata(
    *,
    attack: AttackDefinition,
    result: Any | None,
    observation: TargetObservation | None,
    policy_violation: bool,
    evidence_surface: str | None,
    pyrit_score: bool | None,
    retryable: bool,
) -> dict[str, Any]:
    last_score = getattr(result, "last_score", None)
    return {
        "schema_version": FINDING_SCHEMA_VERSION,
        "attack_id": attack.attack_id,
        "objective": attack.objective,
        "risk_category": attack.risk_category,
        "attack_strategy": attack.attack_strategy,
        "harm_categories": list(attack.harm_categories),
        "labels": attack.labels,
        "finding": {
            "policy_violation": policy_violation,
            "evidence_surface": evidence_surface,
            "pyrit_score": pyrit_score,
            "trajectory_only": policy_violation and pyrit_score is False,
            "retryable": retryable,
        },
        "pyrit": {
            "version": PYRIT_VERSION,
            "conversation_id": str(getattr(result, "conversation_id", "") or ""),
            "attack_result_id": str(getattr(result, "attack_result_id", "") or ""),
            "outcome": _enum_value(getattr(result, "outcome", "")),
            "outcome_reason": str(getattr(result, "outcome_reason", "") or ""),
            "executed_turns": int(getattr(result, "executed_turns", 0) or 0),
            "execution_time_ms": int(
                getattr(result, "execution_time_ms", 0) or 0
            ),
            "targeted_harm_categories": list(
                getattr(result, "targeted_harm_categories", []) or []
            ),
            "score_type": str(getattr(last_score, "score_type", "") or ""),
            "score_rationale": str(
                getattr(last_score, "score_rationale", "") or ""
            ),
        },
        "target_runtime_mode": (
            observation.runtime_mode if observation is not None else None
        ),
    }


def _skipped_score_row(
    *,
    attack: AttackDefinition,
    target: str,
    red_team: dict[str, Any],
    error: str,
) -> dict[str, Any]:
    return {
        "type": "prompt",
        "test_case_id": attack.attack_id,
        "behavior": attack.risk_category,
        "judge_model": f"pyrit/SubStringScorer@{PYRIT_VERSION}+ASSERT-trajectory",
        "target": target,
        "tester_model": "",
        "score_keys": ["policy_violation", "overrefusal"],
        "not_applicable_score_keys": [],
        "judge_status": "scoring_skipped",
        "judge_error": error,
        "dimensions": attack_dimensions(attack),
        "red_team": red_team,
        "verdict": {},
    }


async def _execute_attacks(
    *,
    attacks: list[AttackDefinition],
    plan: AttackPlan,
    target: TargetConfig,
    evaluation: EvaluationConfig,
    config_path: Path,
) -> list[ExecutedAttack]:
    pyrit = _load_pyrit()
    await pyrit.initialize_pyrit_async(
        memory_db_type=pyrit.IN_MEMORY,
        env_files=[],
        load_defaults=False,
        silent=True,
    )

    target_id = _target_identifier(target)
    attack_by_objective = {attack.objective: attack for attack in attacks}
    observations: dict[str, TargetObservation] = {}
    observations_by_objective: dict[str, TargetObservation] = {}
    auth_errors: dict[str, LLMAuthError] = {}

    class AssertPromptTarget(pyrit.PromptTarget):
        def __init__(self, *, target_config: TargetConfig) -> None:
            self._target_config = target_config
            super().__init__(
                endpoint="assert://target",
                model_name=target_id,
            )

        async def _send_prompt_to_target_async(
            self,
            *,
            normalized_conversation: list[Any],
        ) -> list[Any]:
            request = normalized_conversation[-1].get_piece()
            objective = str(request.original_value or request.converted_value or "")
            attack_definition = attack_by_objective[objective]
            if auth_errors:
                raise next(iter(auth_errors.values()))
            runtime = build_target_session(
                target=self._target_config,
                test_case_payload={
                    "description": objective,
                    "red_team": {
                        "attack_id": attack_definition.attack_id,
                        "risk_category": attack_definition.risk_category,
                        "attack_strategy": attack_definition.attack_strategy,
                    },
                },
                inference=evaluation.inference,
                max_tokens=DEFAULT_INFERENCE_MAX_TOKENS,
                config_path=config_path,
                call_label=f"red_team:{attack_definition.attack_id}",
            )
            transcript = Transcript(
                metadata=TranscriptMetadata(
                    kind="prompt",
                    test_case_id=attack_definition.attack_id,
                    behavior=attack_definition.risk_category,
                    target=target_id,
                    tester_model="",
                    dimensions=attack_dimensions(attack_definition),
                )
            )
            messages = [
                Message(
                    role=str(message.get_piece().role),
                    content=str(message.get_piece().converted_value or ""),
                )
                for message in normalized_conversation
            ]
            if (
                self._target_config.system_prompt
                and not any(message.role == "system" for message in messages)
            ):
                messages.insert(
                    0,
                    Message(
                        role="system",
                        content=self._target_config.system_prompt,
                    ),
                )
            if messages and messages[0].role == "system":
                _record_system_message(transcript, messages[0].text or "")

            runtime_result = None
            runtime_error: Exception | None = None
            close_error: Exception | None = None
            try:
                await runtime.open()
                runtime_result = await runtime.run_turn(messages)
            except Exception as exc:  # noqa: BLE001
                runtime_error = exc
            finally:
                try:
                    await runtime.close()
                except Exception as exc:  # noqa: BLE001
                    close_error = exc
                _record_runtime_metadata(
                    transcript,
                    runtime=runtime,
                    status="close_failed" if close_error is not None else "closed",
                    error=close_error,
                )

            conversation_id = str(request.conversation_id)
            if runtime_error is not None:
                is_input_refusal = isinstance(runtime_error, LLMInputError)
                label = "TARGET INPUT REFUSED" if is_input_refusal else "TARGET ERROR"
                transcript.add_event(
                    TranscriptEvent(
                        view=["target", "combined"],
                        actor="system",
                        edit=AddMessageEdit(
                            message=TranscriptMessage(
                                role="system",
                                content=(
                                    f"[{label}: "
                                    f"{type(runtime_error).__name__}: {runtime_error}]"
                                ),
                            )
                        ),
                    )
                )
                transcript.stop_reason = (
                    "target_input_refused" if is_input_refusal else "target_error"
                )
                observation = TargetObservation(
                    transcript=transcript,
                    runtime_mode=runtime.runtime_mode,
                    error=f"{type(runtime_error).__name__}: {runtime_error}",
                    tool_evidence_available=False,
                )
                observations[conversation_id] = observation
                observations_by_objective[objective] = observation
                if is_input_refusal:
                    return [
                        pyrit.construct_response_from_request(
                            request=request,
                            response_text_pieces=[
                                f"[TARGET INPUT REFUSED: {runtime_error}]"
                            ],
                        )
                    ]
                if isinstance(runtime_error, LLMAuthError):
                    auth_errors[objective] = runtime_error
                raise runtime_error

            if runtime_result is None:
                raise RuntimeError("Red-team target did not produce a runtime result.")
            _record_interaction_messages(
                transcript,
                interaction_messages=runtime_result.interaction_messages,
                llm_call_ids=_append_llm_calls(
                    transcript,
                    runtime_result.llm_calls,
                ),
            )
            transcript.stop_reason = (
                "runtime_close_error" if close_error is not None else "completed"
            )
            raw_result = runtime_result.raw if isinstance(runtime_result.raw, dict) else {}
            trace_events = raw_result.get("trace_events")
            tool_evidence_available = (
                bool(trace_events)
                if runtime.runtime_mode == "otel_traced"
                else True
            )
            observation = TargetObservation(
                transcript=transcript,
                runtime_mode=runtime.runtime_mode,
                error=str(close_error) if close_error is not None else None,
                tool_evidence_available=tool_evidence_available,
            )
            observations[conversation_id] = observation
            observations_by_objective[objective] = observation
            return [
                pyrit.construct_response_from_request(
                    request=request,
                    response_text_pieces=[runtime_result.text or ""],
                )
            ]

    prompt_target = AssertPromptTarget(target_config=target)
    scorer = pyrit.SubStringScorer(substring=plan.scorer_value)
    attack_strategy = pyrit.PromptSendingAttack(
        objective_target=prompt_target,
        attack_scoring_config=pyrit.AttackScoringConfig(
            objective_scorer=scorer,
        ),
    )
    effective_concurrency = evaluation.inference.concurrency
    if effective_concurrency != 1:
        log.warning(
            "[red_team] The current attack path executes sequentially; using "
            "concurrency=1 instead of %d",
            effective_concurrency,
        )
        effective_concurrency = 1

    execution = await pyrit.AttackExecutor(
        max_concurrency=effective_concurrency
    ).execute_attack_async(
        attack=attack_strategy,
        objectives=[attack.objective for attack in attacks],
        field_overrides=[
            {
                "memory_labels": {
                    **attack.labels,
                    "attack_id": attack.attack_id,
                    "risk_category": attack.risk_category,
                    "attack_strategy": attack.attack_strategy,
                },
                "targeted_harm_categories": list(attack.harm_categories),
            }
            for attack in attacks
        ],
        return_partial_on_failure=True,
    )
    if auth_errors:
        raise next(iter(auth_errors.values()))

    completed_by_index = {
        input_index: result
        for input_index, result in zip(
            execution.input_indices,
            execution.completed_results,
            strict=True,
        )
    }
    incomplete_by_objective = dict(execution.incomplete_objectives)
    executed: list[ExecutedAttack] = []
    for index, attack_definition in enumerate(attacks):
        result = completed_by_index.get(index)
        if result is None:
            exception = incomplete_by_objective.get(attack_definition.objective)
            observation = observations_by_objective.get(attack_definition.objective)
            error = (
                f"{type(exception).__name__}: {exception}"
                if exception is not None
                else "PyRIT did not return a completed result for this objective."
            )
            executed.append(
                ExecutedAttack(
                    attack=attack_definition,
                    result=None,
                    observation=observation,
                    error=error,
                    retryable=True,
                )
            )
            continue
        conversation_id = str(getattr(result, "conversation_id", "") or "")
        observation = observations.get(conversation_id)
        executed.append(
            ExecutedAttack(
                attack=attack_definition,
                result=result,
                observation=observation,
                error=(
                    str(getattr(result, "error_message", "") or "")
                    or (observation.error if observation is not None else None)
                ),
                retryable=bool(
                    (
                        str(getattr(result, "error_message", "") or "")
                        or (
                            observation.error
                            if observation is not None
                            and observation.transcript.stop_reason
                            != "target_input_refused"
                            else None
                        )
                    )
                ),
            )
        )
    return executed


async def run_red_team(
    *,
    attacks_path: str,
    save_dir: str,
    suite_root: str,
    target: TargetConfig,
    evaluation: EvaluationConfig,
    config_path: Path,
    forced: bool = False,
) -> dict[str, Any]:
    resolved_attacks_path = Path(attacks_path).resolve()
    plan = load_attack_plan(resolved_attacks_path)
    _validate_evidence_capability(plan=plan, target=target)
    resolved_suite_root = Path(suite_root).resolve()
    resolved_suite_root.mkdir(parents=True, exist_ok=True)
    _write_stable_suite_inputs(suite_root=resolved_suite_root, plan=plan)

    out_dir = Path(save_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    inference_set_path = out_dir / INFERENCE_SET_FILE
    scores_path = out_dir / SCORES_FILE
    hash_path = out_dir / _CONFIG_HASH_FILE
    config_hash = _config_fingerprint(
        attacks_path=resolved_attacks_path,
        target=target,
        evaluation=evaluation,
    )
    stored_hash = (
        hash_path.read_text(encoding="utf-8").strip()
        if hash_path.exists()
        else None
    )
    if forced or (stored_hash is not None and stored_hash != config_hash):
        if stored_hash is not None and stored_hash != config_hash and not forced:
            log.warning(
                "[red_team] Attack data or target config changed; discarding prior "
                "run findings and starting fresh"
            )
        for path in (inference_set_path, scores_path):
            if path.exists():
                path.unlink()

    inference_rows = load_jsonl(inference_set_path)
    score_rows = load_jsonl(scores_path)
    completed_inference = {
        str(row.get("test_case_id"))
        for row in inference_rows
        if row.get("test_case_id")
    }
    completed_scores = {
        str(row.get("test_case_id"))
        for row in score_rows
        if row.get("test_case_id")
        and (
            row.get("judge_status") == "ok"
            or (
                row.get("judge_status") == "scoring_skipped"
                and (((row.get("red_team") or {}).get("finding") or {}).get("retryable"))
                is False
            )
        )
    }
    completed_ids = completed_inference.intersection(completed_scores)
    if completed_inference != completed_scores:
        write_jsonl(
            inference_set_path,
            [
                row
                for row in inference_rows
                if str(row.get("test_case_id") or "") in completed_ids
            ],
        )
        write_jsonl(
            scores_path,
            [
                row
                for row in score_rows
                if str(row.get("test_case_id") or "") in completed_ids
            ],
        )
    pending = [
        attack for attack in plan.attacks if attack.attack_id not in completed_ids
    ]
    hash_path.write_text(config_hash, encoding="utf-8")

    executed = await _execute_attacks(
        attacks=pending,
        plan=plan,
        target=target,
        evaluation=evaluation,
        config_path=config_path,
    ) if pending else []

    target_id = _target_identifier(target)
    for item in executed:
        attack_definition = item.attack
        result = item.result
        observation = item.observation
        pyrit_score = _pyrit_score_value(result)
        scoring_error: str | None = None
        retryable = item.retryable
        if observation is not None:
            policy_violation, evidence_surface, evidence_turns = _finding_evidence(
                observation.transcript,
                substring=plan.scorer_value,
                outbound_sinks=plan.outbound_sinks,
            )
            if observation.transcript.stop_reason == "target_input_refused":
                scoring_error = item.error or "Target input was refused."
                retryable = False
            elif item.error:
                scoring_error = item.error
                retryable = True
            elif (
                plan.outbound_sinks
                and observation.runtime_mode == "otel_traced"
                and not observation.tool_evidence_available
            ):
                scoring_error = (
                    "The traced callable emitted no spans, so outbound tool "
                    "evidence could not be evaluated."
                )
                retryable = True
            elif not policy_violation:
                sink_error = _sink_evidence_error(
                    observation.transcript,
                    outbound_sinks=plan.outbound_sinks,
                    substring=plan.scorer_value,
                )
                if sink_error is not None:
                    scoring_error, retryable = sink_error
        else:
            policy_violation, evidence_surface, evidence_turns = False, None, []
            scoring_error = item.error or "Target execution did not produce an observation."
            retryable = True
        red_team = _red_team_metadata(
            attack=attack_definition,
            result=result,
            observation=observation,
            policy_violation=policy_violation,
            evidence_surface=evidence_surface,
            pyrit_score=pyrit_score,
            retryable=retryable,
        )

        if observation is not None:
            inference_row = observation.transcript.to_dict()
        else:
            transcript = Transcript(
                metadata=TranscriptMetadata(
                    kind="prompt",
                    test_case_id=attack_definition.attack_id,
                    behavior=attack_definition.risk_category,
                    target=target_id,
                    tester_model="",
                    dimensions=attack_dimensions(attack_definition),
                ),
                events=[
                    TranscriptEvent(
                        view=["target", "combined"],
                        actor="system",
                        edit=AddMessageEdit(
                            message=TranscriptMessage(
                                role="system",
                                content=f"[RED TEAM ERROR: {item.error or 'unknown error'}]",
                            )
                        ),
                    )
                ],
                stop_reason="target_error",
            )
            inference_row = transcript.to_dict()
        inference_row["red_team"] = red_team
        append_jsonl_row(inference_set_path, inference_row)

        if scoring_error is not None:
            score_row = _skipped_score_row(
                attack=attack_definition,
                target=target_id,
                red_team=red_team,
                error=scoring_error,
            )
        else:
            score_row = build_score_row(
                attack=attack_definition,
                target=target_id,
                red_team=red_team,
                risk_category=next(iter(plan.risk_categories.values())),
                policy_violation=policy_violation,
                evidence_turns=evidence_turns,
                evidence_surface=evidence_surface,
            )
            if (
                red_team["finding"]["trajectory_only"]
                and policy_violation
                and pyrit_score is False
            ):
                log.info(
                    "[red_team] ASSERT captured a trajectory-only finding for "
                    "attack %s",
                    attack_definition.attack_id,
                )
        append_jsonl_row(scores_path, score_row)

    build_run_viewer_artifacts(out_dir, suite_dir=resolved_suite_root)
    final_scores = load_jsonl(scores_path)
    findings = sum(
        1
        for row in final_scores
        if bool(((row.get("verdict") or {}).get("dimensions") or {}).get("policy_violation"))
    )
    errors = sum(
        1
        for row in final_scores
        if row.get("judge_status") == "scoring_skipped"
        and ((((row.get("red_team") or {}).get("finding") or {}).get("retryable")))
    )
    skipped = sum(
        1
        for row in final_scores
        if row.get("judge_status") == "scoring_skipped"
        and (
            (((row.get("red_team") or {}).get("finding") or {}).get("retryable"))
            is False
        )
    )
    trajectory_only_findings = sum(
        1
        for row in final_scores
        if bool(((row.get("verdict") or {}).get("dimensions") or {}).get("policy_violation"))
        and (
            (((row.get("red_team") or {}).get("finding") or {}).get("pyrit_score"))
            is False
        )
    )
    if errors:
        raise RuntimeError(
            f"{errors} red-team attack(s) failed before producing a complete finding."
        )
    return {
        "inference_set_path": str(inference_set_path),
        "scores_path": str(scores_path),
        "count": len(completed_ids) + len(executed),
        "new_count": len(executed),
        "cached_count": len(completed_ids),
        "findings": findings,
        "errored_count": errors,
        "skipped_count": skipped,
        "trajectory_only_findings": trajectory_only_findings,
    }


async def run(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate config and run the PyRIT red-team workflow."""
    target = ctx.get("target")
    evaluation = ctx.get("evaluation")
    if target is None or evaluation is None:
        raise ValueError("red_team requires a target and runtime configuration")
    cfg = resolve_stage_paths(
        {
            "attacks_path": raw_cfg.get("attacks_path"),
            "save_dir": raw_cfg.get("save_dir") or str(ctx["run_root"]),
        },
        cfg_path=ctx["config_path"],
        artifacts_root=ctx["artifacts_root"],
    )
    preflight_plan = load_attack_plan(Path(cfg["attacks_path"]).resolve())
    _validate_evidence_capability(
        plan=preflight_plan,
        target=target,
    )
    preflight_suite_root = Path(ctx["suite_root"]).resolve()
    preflight_suite_root.mkdir(parents=True, exist_ok=True)
    _write_stable_suite_inputs(
        suite_root=preflight_suite_root,
        plan=preflight_plan,
    )
    run_root = Path(ctx["run_root"]).resolve()
    attack_snapshot_dir = run_root / ".red_team"
    attack_snapshot_dir.mkdir(parents=True, exist_ok=True)
    attack_snapshot_path = attack_snapshot_dir / Path(cfg["attacks_path"]).name
    resolved_attacks_path = Path(cfg["attacks_path"]).resolve()
    if resolved_attacks_path != attack_snapshot_path.resolve():
        shutil.copy2(resolved_attacks_path, attack_snapshot_path)
    saved_config_path = run_root / "config.yaml"
    if saved_config_path.exists():
        saved_config = yaml.safe_load(saved_config_path.read_text(encoding="utf-8"))
        if isinstance(saved_config, dict):
            saved_config["suite"] = ctx["suite_id"]
            saved_config["run"] = ctx["run_id"]
            saved_config["results_dir"] = str(ctx["results_dir"])
            pipeline = saved_config.get("pipeline")
            if isinstance(pipeline, dict):
                red_team_config = dict(raw_cfg)
                red_team_config["attacks_path"] = str(
                    Path(".red_team") / attack_snapshot_path.name
                )
                pipeline["red_team"] = red_team_config
                saved_config_path.write_text(
                    yaml.safe_dump(saved_config, sort_keys=False),
                    encoding="utf-8",
                )
    result = await run_red_team(
        attacks_path=cfg["attacks_path"],
        save_dir=cfg["save_dir"],
        suite_root=str(ctx["suite_root"]),
        target=target,
        evaluation=evaluation,
        config_path=ctx["config_path"],
        forced=bool(ctx.get("_stage_forced", False)),
    )
    return {
        "inference_set_path": result["inference_set_path"],
        "scores_path": result["scores_path"],
        "_summary": {
            "count": result.get("count", 0),
            "new_count": result.get("new_count", 0),
            "cached_count": result.get("cached_count", 0),
            "findings": result.get("findings", 0),
            "trajectory_only_findings": result.get(
                "trajectory_only_findings",
                0,
            ),
            "skipped_count": result.get("skipped_count", 0),
            "errored_count": result.get("errored_count", 0),
        },
    }


__all__ = ["run", "run_red_team"]
