"""Run unified prompt/scenario rollouts and write transcripts."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import hashlib
import json as json_module
import logging
import traceback
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from p2m.config import resolve_stage_paths
from p2m.core.config_model import (
    DEFAULT_MODEL_TIMEOUT_S,
    DEFAULT_ROLLOUT_MAX_TOKENS,
    EvaluationConfig,
    ModelConfig,
    RolloutConfig,
    TargetConfig,
)
from p2m.core.io import (
    TRANSCRIPTS_FILE,
    append_jsonl_row,
    load_jsonl,
    load_prompt_text,
    load_seeds,
    normalize_seed_rows,
    resolve_path,
    write_jsonl,
    row_factors,
)
from p2m.core.model_client import GenerateOptions, Message, ModelResponse, build_llm_call_trace, generate
from p2m.core.session import (
    ExternalSession,
    HostedSession,
    SimulatedResolver,
    TurnResult,
)
from p2m.core.tool_backend import ToolBackendResolver, inspect_tool_module
from p2m.core.tools import load_toolset_file, normalize_tool_defs
from p2m.core.transcript import (
    AddMessageEdit,
    Message as TranscriptMessage,
    SetSystemMessageEdit,
    ToolCallEdit,
    Transcript,
    TranscriptEvent,
    TranscriptMetadata,
)
from p2m.viewer_read_model import build_run_viewer_artifacts

SCOPE = "run"
SUITE_OUTPUT = None

_AUDITOR_RETRY_GUIDANCE = "Your last reply was invalid. Write only the user's next visible message in character."

_ROLLOUT_CONFIG_HASH_FILE = ".rollout_config_hash"


def _rollout_config_fingerprint(
    target: TargetConfig,
    evaluation: EvaluationConfig | None,
    max_tokens: int,
) -> str:
    """Deterministic hash of config values that affect rollout output."""
    target_name = target.model.name if isinstance(target.model, ModelConfig) else (target.connector or "")
    key = json_module.dumps(
        {
            "target": target_name,
            "max_tokens": max_tokens,
            "max_turns": evaluation.rollout.max_turns if evaluation else None,
            "concurrency": evaluation.rollout.concurrency if evaluation else None,
            "auditor": evaluation.auditor.model.name if evaluation and evaluation.auditor else None,
        },
        sort_keys=True,
    )
    return hashlib.sha256(key.encode()).hexdigest()[:16]

AUDITOR_SYSTEM_PROMPT = load_prompt_text("rollout_auditor_system.md")
TOOL_SIM_PROMPT = load_prompt_text("rollout_toolsim_user.md")


def _infer_tool_source(target: TargetConfig) -> str:
    """Infer tool_source from the target config.

    - simulator without toolset -> per_seed (tools come from each seed row)
    - everything else → runtime (tools come from a tool module or fixed toolset)
    """
    if target.tools is not None and target.tools.simulator and not target.tools.toolset:
        return "per_seed"
    return "runtime"


def _record_system_message(transcript: Transcript, system_message: str) -> None:
    """Record a target system-message update in the transcript."""
    transcript.add_event(TranscriptEvent(
        view=["system", "target", "combined"],
        actor="auditor",
        edit=SetSystemMessageEdit(message=TranscriptMessage(role="system", content=system_message)),
    ))
    transcript.add_event(TranscriptEvent(
        view=["system", "combined"],
        actor="system",
        edit=AddMessageEdit(message=TranscriptMessage(role="system", content="[System message updated]")),
    ))

def _record_interaction_messages(
    transcript: Transcript,
    *,
    interaction_messages: list[dict[str, Any]],
    llm_call_ids: dict[int, str] | None = None,
    skip_initial_user_content: str | None = None,
) -> None:
    """Copy runtime interaction messages into the transcript."""
    skipped_initial_user = False
    pending_tool_calls: dict[str, tuple[str, dict[str, Any]]] = {}
    pending_target_llm_call_id: str | None = None

    for message in interaction_messages:
        role = message.get("role")
        content = str(message.get("content") or "")
        llm_call_id = None
        llm_call_index = message.get("llm_call_index")
        if isinstance(llm_call_index, int) and llm_call_ids is not None:
            llm_call_id = llm_call_ids.get(llm_call_index)

        if role == "system":
            continue

        if role == "user":
            if (
                not skipped_initial_user
                and skip_initial_user_content is not None
                and content == skip_initial_user_content
            ):
                skipped_initial_user = True
                continue
            message_id = f"event:{len(transcript.events)}"
            transcript.add_event(TranscriptEvent(
                view=["target", "combined"],
                actor="target",
                edit=AddMessageEdit(message=TranscriptMessage(role="user", content=content)),
                raw=message.get("raw") if isinstance(message.get("raw"), dict) else None,
            ))
            if llm_call_id is not None:
                transcript.link_llm_call_to_message(llm_call_id, message_id)
            continue

        if role == "assistant":
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                pending_target_llm_call_id = llm_call_id
                for tool_call in tool_calls:
                    if not isinstance(tool_call, dict):
                        continue
                    tool_call_id = str(tool_call.get("id") or "")
                    tool_name = str(tool_call.get("function") or "tool")
                    tool_args = tool_call.get("arguments") if isinstance(tool_call.get("arguments"), dict) else {}
                    if tool_call_id:
                        pending_tool_calls[tool_call_id] = (tool_name, tool_args)
            if content:
                message_id = f"event:{len(transcript.events)}"
                transcript.add_event(TranscriptEvent(
                    view=["target", "combined"],
                    actor="target",
                    edit=AddMessageEdit(message=TranscriptMessage(role="assistant", content=content)),
                    raw=message.get("raw") if isinstance(message.get("raw"), dict) else None,
                ))
                if llm_call_id is not None:
                    transcript.link_llm_call_to_message(llm_call_id, message_id)
            continue

        if role == "tool":
            tool_call_id = str(message.get("tool_call_id") or "")
            default_name = str(message.get("function") or "tool")
            default_args = message.get("arguments") if isinstance(message.get("arguments"), dict) else {}
            tool_name, tool_args = pending_tool_calls.get(tool_call_id, (default_name, default_args))
            message_id = f"event:{len(transcript.events)}"
            transcript.add_event(TranscriptEvent(
                view=["target", "combined"],
                actor="tool",
                edit=ToolCallEdit(
                    tool_name=tool_name,
                    tool_args=tool_args,
                    tool_result=content,
                ),
                raw=message.get("raw") if isinstance(message.get("raw"), dict) else None,
            ))
            if llm_call_id is not None:
                transcript.link_llm_call_to_message(llm_call_id, message_id)
            if pending_target_llm_call_id is not None:
                transcript.link_llm_call_to_message(pending_target_llm_call_id, message_id)


def _append_llm_calls(transcript: Transcript, llm_calls: list[dict[str, Any]]) -> dict[int, str]:
    """Append owned LLM calls to the transcript and return local-index to call-id mapping."""
    call_id_by_index: dict[int, str] = {}
    for index, llm_call in enumerate(llm_calls):
        if not isinstance(llm_call, dict):
            continue
        call_id_by_index[index] = transcript.append_llm_call(
            source=str(llm_call.get("source") or ""),
            api_mode=str(llm_call.get("api_mode") or ""),
            request=llm_call.get("request"),
            response=llm_call.get("response"),
            derived=llm_call.get("derived") if isinstance(llm_call.get("derived"), dict) else {},
        )
    return call_id_by_index


async def _run_with_runtime(
    runtime: HostedSession | ExternalSession,
    runner: Callable[[HostedSession | ExternalSession], Awaitable[Any]],
) -> tuple[Any, Exception | None]:
    result: Any = None
    runtime_error: Exception | None = None
    close_error: Exception | None = None
    try:
        await runtime.open()
        result = await runner(runtime)
    except Exception as exc:  # noqa: BLE001
        runtime_error = exc
    finally:
        try:
            await runtime.close()
        except Exception as exc:  # noqa: BLE001
            close_error = exc
    if runtime_error is not None:
        raise runtime_error
    return result, close_error

def _prepare_seeds(
    rows: list[dict[str, Any]],
    *,
    tool_source: str,
    fixed_system_prompt: str | None,
) -> list[dict[str, Any]]:
    """Validate canonical seed rows and normalize prompt/scenario-specific fields."""
    seeds: list[dict[str, Any]] = []
    nested_seed_fields = {"prompt", "description", "system_prompt", "title", "tools", "state"}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"seed at index {index} must be an object")

        kind = row.get("kind")
        if kind not in {"prompt", "scenario"}:
            raise ValueError(f"seed at index {index} must declare kind 'prompt' or 'scenario'")

        seed_payload = row.get("seed")
        if not isinstance(seed_payload, dict):
            raise ValueError(f"{kind} seed at index {index} requires a seed object")
        seed_row = dict(row)
        normalized_payload = dict(seed_payload)
        system_prompt = str(normalized_payload.get("system_prompt") or "").strip() or None
        if system_prompt is None:
            normalized_payload.pop("system_prompt", None)
        else:
            normalized_payload["system_prompt"] = system_prompt
        if fixed_system_prompt and system_prompt is not None:
            raise ValueError("target.system_prompt cannot be combined with non-empty seed.system_prompt")
        tools = normalized_payload.get("tools")
        if tool_source == "per_seed":
            if not isinstance(tools, list) or not tools:
                raise ValueError("seed.tools is required when tool_source=per_seed")
            normalize_tool_defs(tools)
        elif tools is not None:
            raise ValueError("seed.tools is only allowed when tool_source=per_seed")
        seed_row["seed"] = normalized_payload
        if kind == "prompt":
            invalid_fields = sorted(field for field in nested_seed_fields if field in row)
            if invalid_fields:
                raise ValueError(
                    f"prompt seed at index {index} must move {', '.join(invalid_fields)} under seed"
                )
            if not str(normalized_payload.get("description") or "").strip():
                raise ValueError(
                    f"prompt seed at index {index} requires a non-empty seed.description"
                )
        elif not str(normalized_payload.get("description") or "").strip():
            raise ValueError(
                f"scenario seed at index {index} requires a non-empty seed.description"
            )
        seeds.append(seed_row)
    return seeds


def _build_hosted_session(
    *,
    model: str,
    tools_config: dict[str, Any] | None,
    scenario: dict[str, Any],
    generate_options: GenerateOptions,
    max_tool_calls: int,
    synthetic_prompt_template: str,
    tool_timeout_s: float | None = None,
    startup_timeout_s: float | None = None,
) -> HostedSession:
    if not tools_config:
        return HostedSession(
            model=model,
            generate_options=generate_options,
            max_tool_calls=max_tool_calls,
            runtime_label="chat",
        )

    config_path = Path(tools_config["_config_path"]) if tools_config.get("_config_path") else None
    module_ref = tools_config.get("module")
    if module_ref is not None:
        if not isinstance(module_ref, str) or not module_ref.strip():
            raise ValueError("tool-module tools require module")
        tools_cls, schemas = inspect_tool_module(module_ref, config_path=config_path)
        return HostedSession(
            model=model,
            generate_options=generate_options,
            tools=schemas,
            resolver=ToolBackendResolver(
                tools_cls,
                scenario,
                tool_timeout_s=tool_timeout_s,
                startup_timeout_s=startup_timeout_s,
            ),
            max_tool_calls=max_tool_calls,
            runtime_label="tool_module",
        )

    toolset_path = tools_config.get("toolset")
    simulator_model = tools_config.get("simulator")
    if toolset_path is not None or simulator_model is not None:
        if not isinstance(simulator_model, str) or not simulator_model.strip():
            raise ValueError("simulated tools require target.tools.simulator")
        tools = scenario.get("tools")
        if tools is None:
            if not isinstance(toolset_path, str) or not toolset_path.strip():
                raise ValueError("simulated tools require target.tools.toolset or seed.tools")
            resolved_path = Path(toolset_path).expanduser()
            if not resolved_path.is_absolute():
                candidates = []
                if config_path is not None:
                    candidates.append((config_path.parent / resolved_path).resolve())
                candidates.append((Path.cwd() / resolved_path).resolve())
                found = next((c for c in candidates if c.exists()), None)
                resolved_path = found if found is not None else candidates[0]
            tools = load_toolset_file(resolved_path)
        return HostedSession(
            model=model,
            generate_options=generate_options,
            tools=list(tools),
            resolver=SimulatedResolver(
                model=simulator_model,
                prompt_template=synthetic_prompt_template,
                scenario=scenario,
                timeout_s=tool_timeout_s,
            ),
            max_tool_calls=max_tool_calls,
            runtime_label="simulated",
        )
    raise ValueError("target.tools must define module or toolset+simulator")


def _build_target_session(
    *,
    target: TargetConfig,
    seed_payload: dict[str, Any],
    rollout: RolloutConfig,
    max_tokens: int,
    config_path: Path | None,
) -> HostedSession | ExternalSession:
    """Create the runtime session for one seed rollout."""
    if target.is_external:
        if not target.connector:
            raise ValueError("external target requires a connector")
        return ExternalSession(
            connector_ref=target.connector,
            scenario=seed_payload,
            startup_timeout_s=rollout.startup_timeout_s,
            message_timeout_s=rollout.tool_timeout_s,
            config_path=config_path,
        )

    if not target.model:
        raise ValueError("hosted target requires target.model")
    target_model = str(target.model.name)
    target_temperature = target.model.temperature
    target_max_tokens = target.model.max_tokens if target.model.max_tokens is not None else max_tokens
    tools_dict = asdict(target.tools) if target.tools else None
    if tools_dict is not None:
        tools_dict["_config_path"] = str(config_path) if config_path else None

    return _build_hosted_session(
        model=target_model,
        tools_config=tools_dict,
        scenario=seed_payload,
        generate_options=GenerateOptions(
            max_tokens=target_max_tokens,
            temperature=target_temperature,
            reasoning_effort=target.model.reasoning_effort,
            timeout_s=DEFAULT_MODEL_TIMEOUT_S,
        ),
        max_tool_calls=rollout.max_tool_calls,
        synthetic_prompt_template=TOOL_SIM_PROMPT,
        tool_timeout_s=rollout.tool_timeout_s,
        startup_timeout_s=rollout.startup_timeout_s,
    )


async def _run_prompt_seed(
    *,
    seed: dict[str, Any],
    target: TargetConfig,
    rollout: RolloutConfig,
    max_tokens: int,
    config_path: Path | None,
) -> Transcript:
    """Run one prompt seed against the target runtime."""
    seed_payload = seed.get("seed")
    if not isinstance(seed_payload, dict):
        raise ValueError("rollout requires each seed row to include a seed object")
    runtime = _build_target_session(
        target=target,
        seed_payload=seed_payload,
        rollout=rollout,
        max_tokens=max_tokens,
        config_path=config_path,
    )
    target_id = str(target.model.name) if target.model else (target.connector or "")
    transcript = Transcript(
        metadata=TranscriptMetadata(
            kind="prompt",
            seed_id=str(seed["seed_id"]),
            concept=str(seed.get("concept") or ""),
            target=target_id,
            auditor_model="",
            target_reasoning_effort=target.model.reasoning_effort if target.model else None,
            factors=row_factors(seed),
        )
    )
    prompt = str(seed_payload.get("description") or "").strip()
    if not prompt:
        raise ValueError("prompt seeds require a non-empty seed.description")
    target_system_prompt = str(target.system_prompt or "").strip() or None
    effective_system_message = target_system_prompt or (str(seed_payload.get("system_prompt") or "").strip() or None)
    initial_messages: list[Message] = []
    if effective_system_message:
        initial_messages.append(Message(role="system", content=effective_system_message))
    initial_messages.append(Message(role="user", content=prompt))

    if initial_messages and initial_messages[0].role == "system":
        _record_system_message(transcript, initial_messages[0].text or "")

    async def _run_prompt_turn(session: HostedSession | ExternalSession) -> TurnResult:
        return await session.run_turn(initial_messages)

    runtime_result, close_error = await _run_with_runtime(runtime, _run_prompt_turn)
    if runtime_result is None:
        raise RuntimeError("Prompt rollout did not produce a runtime result.")
    _record_interaction_messages(
        transcript,
        interaction_messages=runtime_result.interaction_messages,
        llm_call_ids=_append_llm_calls(transcript, runtime_result.llm_calls),
    )
    if close_error is not None:
        transcript.stop_reason = "runtime_close_error"
        return transcript
    transcript.stop_reason = "completed"
    return transcript


async def _run_auditor_target_loop(
    *,
    transcript: Transcript,
    auditor_messages: list[Message],
    target_messages: list[Message],
    auditor_model: str,
    auditor_temperature: float | None,
    auditor_max_tokens: int | None,
    auditor_reasoning_effort: str | None = None,
    target_runtime: HostedSession | ExternalSession,
    max_turns: int,
) -> tuple[str | None, list[Message], list[Message]]:
    """Run the alternating auditor and target loop for one scenario seed."""
    stop_reason = None

    for turn_index in range(max_turns):
        if stop_reason:
            break

        action_message: str | None = None
        last_error = None
        auditor_response: ModelResponse | None = None

        for attempt in range(3):
            try:
                auditor_response = await generate(
                    auditor_model,
                    auditor_messages,
                    options=GenerateOptions(
                        temperature=auditor_temperature,
                        max_tokens=auditor_max_tokens,
                        reasoning_effort=auditor_reasoning_effort,
                        timeout_s=DEFAULT_MODEL_TIMEOUT_S,
                        extra_kwargs={"extra_body": {"store": True}},
                    ),
                )
                action_message = (auditor_response.text or "").strip()
                if not action_message:
                    raise ValueError("auditor returned an empty user turn")
                if auditor_response.finish_reason == "length":
                    logging.warning(
                        "Auditor response truncated (finish_reason=length) at turn %d/%d",
                        turn_index + 1,
                        max_turns,
                    )
                # Remove any retry guidance messages before continuing.
                auditor_messages = [m for m in auditor_messages if m.content != _AUDITOR_RETRY_GUIDANCE]
                auditor_messages.append(auditor_response.message)
                break
            except Exception as exc:
                last_error = str(exc)
                if attempt < 2:
                    auditor_messages.append(
                        Message(role="system", content=_AUDITOR_RETRY_GUIDANCE)
                    )
                if attempt == 2:
                    stop_reason = "invalid_auditor_turn"
                    transcript.add_event(TranscriptEvent(
                        view=["system", "combined"],
                        actor="system",
                        edit=AddMessageEdit(message=TranscriptMessage(role="system", content=f"[AUDITOR ERROR: {last_error}]")),
                    ))
                    break

        if stop_reason:
            break

        assert auditor_response is not None
        assert action_message is not None

        target_messages.append(Message(role="user", content=action_message))
        auditor_call_id = transcript.append_llm_call(
            **build_llm_call_trace(auditor_response, source="auditor")
        )
        message_id = f"event:{len(transcript.events)}"
        transcript.add_event(TranscriptEvent(
            view=["target", "combined"],
            actor="auditor",
            edit=AddMessageEdit(message=TranscriptMessage(role="user", content=action_message)),
        ))
        transcript.link_llm_call_to_message(auditor_call_id, message_id)

        try:
            runtime_result = await target_runtime.run_turn(target_messages)
            target_messages = list(runtime_result.state_messages)

            if runtime_result.interaction_messages:
                _record_interaction_messages(
                    transcript,
                    interaction_messages=runtime_result.interaction_messages,
                    llm_call_ids=_append_llm_calls(transcript, runtime_result.llm_calls),
                    skip_initial_user_content=action_message,
                )
            else:
                target_call_ids = _append_llm_calls(transcript, runtime_result.llm_calls)
                llm_call_id = target_call_ids.get(0)
                message_id = f"event:{len(transcript.events)}"
                transcript.add_event(TranscriptEvent(
                    view=["target", "combined"],
                    actor="target",
                    edit=AddMessageEdit(
                        message=TranscriptMessage(role="assistant", content=runtime_result.text or ""),
                    ),
                ))
                if llm_call_id is not None:
                    transcript.link_llm_call_to_message(llm_call_id, message_id)

            auditor_messages.append(
                Message(
                    role="user",
                    content=(
                        f"[Turn {turn_index + 1}/{max_turns}]\n"
                        f"<target_response>\n{runtime_result.text or ''}\n</target_response>"
                    ),
                )
            )
            if runtime_result.finish_reason == "length":
                logging.warning(
                    "Target response truncated (finish_reason=length) at turn %d/%d",
                    turn_index + 1,
                    max_turns,
                )
        except Exception as exc:
            tb = traceback.format_exc()
            transcript.add_event(TranscriptEvent(
                view=["target", "combined"],
                actor="system",
                edit=AddMessageEdit(message=TranscriptMessage(role="system", content=f"[TARGET ERROR: {exc}]\n{tb}")),
            ))
            auditor_messages.append(Message(role="user", content=f"<target_error>{exc}</target_error>"))
            stop_reason = "target_error"
            break

    return stop_reason, auditor_messages, target_messages


async def _run_scenario_seed(
    *,
    seed: dict[str, Any],
    target: TargetConfig,
    evaluation: EvaluationConfig,
    max_tokens: int,
    config_path: Path | None,
) -> Transcript:
    """Run one scenario seed and capture its transcript."""
    auditor = evaluation.auditor
    if auditor is None:
        raise ValueError("scenario rollout requires evaluation.auditor")

    seed_data = seed["seed"]
    runtime = _build_target_session(
        target=target,
        seed_payload=seed_data,
        rollout=evaluation.rollout,
        max_tokens=max_tokens,
        config_path=config_path,
    )
    transcript = Transcript(
        metadata=TranscriptMetadata(
            kind="scenario",
            seed_id=str(seed["seed_id"]),
            concept=str(seed.get("concept") or ""),
            target=str(target.model.name) if target.model else (target.connector or ""),
            auditor_model=str(auditor.model.name),
            target_reasoning_effort=target.model.reasoning_effort if target.model else None,
            auditor_reasoning_effort=auditor.model.reasoning_effort,
            factors=row_factors(seed),
        )
    )

    system_prompt = AUDITOR_SYSTEM_PROMPT.replace("{{description}}", str(seed_data.get("description") or ""))
    system_prompt = system_prompt.replace("{{max_turns}}", str(evaluation.rollout.max_turns))

    target_messages: list[Message] = []
    target_system_prompt = str(target.system_prompt or "").strip() or None
    effective_system_message = target_system_prompt or (str(seed_data.get("system_prompt") or "").strip() or None)
    auditor_messages: list[Message] = [
        Message(role="system", content=system_prompt),
        Message(role="user", content="Begin the conversation now with the user's first message only."),
    ]
    if effective_system_message:
        target_messages.append(Message(role="system", content=effective_system_message))
        _record_system_message(transcript, effective_system_message)

    async def _run_scenario_loop(
        session: HostedSession | ExternalSession,
    ) -> tuple[str | None, list[Message], list[Message]]:
        return await _run_auditor_target_loop(
            transcript=transcript,
            auditor_messages=auditor_messages,
            target_messages=target_messages,
            auditor_model=str(auditor.model.name),
            auditor_temperature=auditor.model.temperature,
            auditor_max_tokens=auditor.model.max_tokens,
            auditor_reasoning_effort=auditor.model.reasoning_effort,
            target_runtime=session,
            max_turns=evaluation.rollout.max_turns,
        )
    loop_result, close_error = await _run_with_runtime(runtime, _run_scenario_loop)
    if loop_result is None:
        raise RuntimeError("Scenario rollout did not produce a loop result.")
    stop_reason, _, _ = loop_result
    transcript.stop_reason = "runtime_close_error" if close_error is not None else (stop_reason or "max_turns")
    return transcript


async def run_rollout(
    *,
    seed_path: str,
    save_dir: str | None = None,
    run_id: str | None = None,
    max_tokens: int | None = None,
    target: TargetConfig,
    evaluation: EvaluationConfig | None = None,
    config_path: Path | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Run all seed rollouts and write the transcript artifact."""
    if not target.model and not target.connector:
        raise ValueError("rollout requires target.model or target.connector")

    tool_source = _infer_tool_source(target)

    if tool_source == "per_seed":
        if not target.model:
            raise ValueError("tool_source=per_seed requires target.model")
        if target.connector:
            raise ValueError("tool_source=per_seed does not support target.connector")
        if target.tools is None or not target.tools.simulator:
            raise ValueError("tool_source=per_seed requires target.tools.simulator")
        if target.tools.module:
            raise ValueError("tool_source=per_seed does not support target.tools.module")
        if target.tools.toolset:
            raise ValueError("tool_source=per_seed does not support target.tools.toolset")
    elif target.tools is not None and target.tools.simulator and not target.tools.toolset:
        raise ValueError("runtime tool_source requires target.tools.toolset when target.tools.simulator is set")
    fixed_system_prompt = str(target.system_prompt or "").strip() or None
    resolved_seed_path = resolve_path(seed_path)
    canonical_rows = normalize_seed_rows(load_seeds(resolved_seed_path, strict=strict))
    seeds_list = _prepare_seeds(
        canonical_rows,
        tool_source=tool_source,
        fixed_system_prompt=fixed_system_prompt,
    )
    if not seeds_list:
        raise ValueError("No seeds found")
    write_jsonl(resolved_seed_path, canonical_rows)

    resolved_run_id = str(run_id or uuid.uuid4().hex[:8]).lower()
    out_dir = resolve_path(save_dir or (Path("artifacts/outputs") / resolved_run_id))
    out_dir.mkdir(parents=True, exist_ok=True)
    resolved_max_tokens = max_tokens if max_tokens is not None else DEFAULT_ROLLOUT_MAX_TOKENS
    rollout = evaluation.rollout if evaluation is not None else RolloutConfig()
    indexed_seeds = list(enumerate(seeds_list))
    transcripts_path = out_dir / TRANSCRIPTS_FILE

    # Resume: load already-completed seed_ids and skip them.
    completed_seed_ids: set[str] = set()
    config_hash = _rollout_config_fingerprint(target, evaluation, resolved_max_tokens)
    config_hash_path = out_dir / _ROLLOUT_CONFIG_HASH_FILE
    if transcripts_path.exists():
        # Check that existing transcripts were produced with the same config.
        stored_hash = config_hash_path.read_text(encoding="utf-8").strip() if config_hash_path.exists() else None
        if stored_hash is not None and stored_hash != config_hash:
            logging.warning(
                "Rollout config changed since last run — discarding %s and starting fresh",
                transcripts_path,
            )
            transcripts_path.unlink()
        else:
            for row in load_jsonl(transcripts_path):
                sid = row.get("seed_id")
                if sid:
                    completed_seed_ids.add(str(sid))
    if completed_seed_ids:
        logging.info(
            "Resuming rollout: %d seeds already completed, skipping",
            len(completed_seed_ids),
        )
    config_hash_path.write_text(config_hash, encoding="utf-8")
    pending_seeds = [
        (i, seed) for i, seed in indexed_seeds
        if str(seed.get("seed_id", "")) not in completed_seed_ids
    ]

    async def _worker(seed: tuple[int, dict[str, Any]]) -> dict[str, Any]:
        """Wrap single-seed rollout so concurrent execution keeps errors structured."""
        output_index, seed_row = seed
        try:
            kind = seed_row["kind"]
            if kind == "prompt":
                transcript = await _run_prompt_seed(
                    seed=seed_row,
                    target=target,
                    rollout=rollout,
                    max_tokens=resolved_max_tokens,
                    config_path=config_path,
                )
            elif kind == "scenario":
                if evaluation is None:
                    raise ValueError("scenario rollout requires evaluation configuration")
                transcript = await _run_scenario_seed(
                    seed=seed_row,
                    target=target,
                    evaluation=evaluation,
                    max_tokens=resolved_max_tokens,
                    config_path=config_path,
                )
            else:
                raise ValueError(f"unsupported seed kind: {kind}")
            return {"output_index": output_index, "transcript_row": transcript.to_dict()}
        except Exception as exc:
            return {"output_index": output_index, "error": exc}

    semaphore = asyncio.Semaphore(max(1, min(rollout.concurrency, len(pending_seeds) or 1)))

    async def _guard(seed: tuple[int, dict[str, Any]]) -> dict[str, Any]:
        async with semaphore:
            return await _worker(seed)

    tasks = [asyncio.create_task(_guard(seed)) for seed in pending_seeds]
    results = []
    errors: list[Exception] = []
    for completed_task in asyncio.as_completed(tasks):
        result = await completed_task
        results.append(result)
        transcript_row = result.get("transcript_row")
        if transcript_row is not None:
            append_jsonl_row(transcripts_path, transcript_row)
        error = result.get("error")
        if error is not None:
            errors.append(error)

    build_run_viewer_artifacts(out_dir)
    if errors:
        raise errors[0]

    return {
        "transcripts_path": str(transcripts_path),
        "run_id": resolved_run_id,
        "count": len(completed_seed_ids) + len(results),
    }


async def run(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate config and run the rollout workflow."""
    target = ctx.get("target")
    if target is None:
        raise ValueError("rollout requires a target")
    cfg = resolve_stage_paths(
        {
            "seed_path": raw_cfg.get("seed_path") or str(Path(ctx["suite_root"]) / "seeds.jsonl"),
            "save_dir": raw_cfg.get("save_dir") or str(ctx["run_root"]),
            "max_tokens": raw_cfg.get("max_tokens", DEFAULT_ROLLOUT_MAX_TOKENS),
            "strict": raw_cfg.get("strict", False) or ctx.get("strict", False),
        },
        cfg_path=ctx["config_path"],
        artifacts_root=ctx["artifacts_root"],
    )
    result = await run_rollout(
        seed_path=cfg["seed_path"],
        save_dir=cfg["save_dir"],
        run_id=ctx["run_id"],
        max_tokens=cfg.get("max_tokens"),
        target=ctx["target"],
        evaluation=ctx.get("evaluation"),
        config_path=ctx["config_path"],
        strict=cfg.get("strict", False),
    )
    return {"transcripts_path": result["transcripts_path"]}
