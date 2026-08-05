# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Materialised health-assistant Prompt Agent (ungoverned baseline callable target).

A **Prompt Agent has no host process**: `health_assistant.yaml` /
`health_assistant_simulated_tools.yaml` declare the target entirely in YAML and the
ASSERT runtime (`assert_ai.core.session.HostedSession`) owns the conversation loop.
There is nothing to wrap, so ACS cannot be attached to it. This module *materialises*
that target as a Python callable so an A/B against a governed variant is possible at
all.

Materialisation strategy — reuse, do not re-implement
----------------------------------------------------
Every part of the target that could drift is taken from ASSERT itself or from the
unmodified YAML, never re-typed here:

* **system prompt / model / temperature / max_tokens / toolset / simulator** are read
  out of the checked-in YAML through ASSERT's own ``parse_target_config`` and the same
  ``default_model`` fallback ``assert_ai/config.py`` applies. Editing the YAML changes
  this callable; nothing is copied.
* **the conversation loop is literally ASSERT's own** ``HostedSession.run_turn`` —
  this module instantiates the real class rather than imitating it, so loop shape,
  per-turn tool-call accounting, the ``max_tool_calls`` cut-off and its
  "Tool call limit reached." messages, and the trailing tool-free completion call are
  identical by construction.
* **tool results come from the real** ``SimulatedResolver`` **with the real
  ``inference_toolsim_user.md`` template**, i.e. the same LLM-simulator path the YAML
  target uses. There is no clinical backend anywhere in scope: every tool result is
  generated text.
* **cross-turn state** (the accumulated message list, including tool messages, and the
  simulator's ``tool_history``) is carried between turns exactly as the inference
  stage carries ``TurnResult.state_messages``, by caching the live ``HostedSession``
  keyed on the conversation prefix.

Known, disclosed divergences (see ``KNOWN_DIVERGENCES``) affect *absolute levels*, not
the ACS delta: the baseline and governed arms run this same module.

Entrypoints
-----------
``chat_modelonly`` / ``chat_simtools`` — ``(message: str, history: list | None) -> str``.
``chat_gentools`` exists only to fail loudly: the generated-tools variant is **not
materialisable** (see the function's docstring).

The governed counterpart is :mod:`examples.prompt_agents.agent_guarded`, which imports
``_chat`` from here and adds ONLY ACS enforcement.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import threading
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - dotenv is optional
    def load_dotenv(*args: Any, **kwargs: Any) -> bool:
        return False


_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

load_dotenv()
load_dotenv(_REPO_ROOT / ".env", override=False)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

os.environ.setdefault("AZURE_API_VERSION", "2024-08-01-preview")

from assert_ai.config import parse_target_config  # noqa: E402
from assert_ai.core.config_model import (  # noqa: E402
    DEFAULT_INFERENCE_MAX_TOOL_CALLS,
    DEFAULT_MODEL_TIMEOUT_S,
)
from assert_ai.core.io import load_prompt_text  # noqa: E402
from assert_ai.core.model_client import GenerateOptions, Message, generate  # noqa: E402
from assert_ai.core.session import HostedSession, SimulatedResolver  # noqa: E402
from assert_ai.core.tools import load_toolset_file  # noqa: E402

# The identical template the inference stage feeds SimulatedResolver
# (assert_ai/stages/inference.py: TOOL_SIM_PROMPT = load_prompt_text(...)).
TOOL_SIM_PROMPT = load_prompt_text("inference_toolsim_user.md")

VARIANT_CONFIGS: dict[str, str] = {
    "modelonly": "health_assistant.yaml",
    "simtools": "health_assistant_simulated_tools.yaml",
    "gentools": "health_assistant_generated_tools.yaml",
}

KNOWN_DIVERGENCES = (
    "The tool simulator's {{description}} slot is the ASSERT test-case description. A "
    "callable target never receives the test-case payload, so prompt cases use the user "
    "message (identical to the description by construction) and scenario cases use the "
    "opening user turn as a proxy for the scenario description.",
    "ASSERT never hands target.system_prompt to a callable, so the system prompt is read "
    "from the YAML by this module instead of being injected by the runtime. Same string, "
    "different delivery path.",
    "Cross-turn continuity is reconstructed by caching the live HostedSession on a hash of "
    "the conversation prefix. The runtime instead threads TurnResult.state_messages "
    "through one long-lived session object. Identical content; a cache miss (never "
    "observed) would silently restart a conversation.",
    "target.trace is deliberately NOT enabled: OTelTracedSession serialises every target "
    "turn behind one global asyncio lock, which makes this scope infeasible. The judge "
    "therefore scores the transcript text rather than trace spans. This lowers what the "
    "judge can see relative to a YAML Prompt Agent run (which surfaces tool calls and "
    "tool results in the transcript) and is a level effect, identical on both arms.",
)


# ── Target resolution: read the YAML through ASSERT's own parser ────────────────

@dataclass(frozen=True)
class _Variant:
    name: str
    config_path: Path
    system_prompt: str
    model: str
    temperature: float | None
    max_tokens: int | None
    max_tool_calls: int
    tools: list[dict[str, Any]] | None
    simulator: str | None

    def describe(self) -> dict[str, Any]:
        return {
            "variant": self.name,
            "yaml": str(self.config_path.relative_to(_REPO_ROOT)).replace("\\", "/"),
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "max_tool_calls": self.max_tool_calls,
            "simulator": self.simulator,
            "tool_names": [tool["name"] for tool in (self.tools or [])],
            "system_prompt_sha256": hashlib.sha256(
                self.system_prompt.encode("utf-8")
            ).hexdigest(),
            "system_prompt_chars": len(self.system_prompt),
        }


def _resolve_variant(name: str) -> _Variant:
    """Resolve a variant from its unmodified YAML using ASSERT's own config parser."""
    config_path = _HERE / VARIANT_CONFIGS[name]
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    pipeline_raw = raw["pipeline"]
    inference_raw = pipeline_raw.get("inference") or {}
    target_raw = dict(inference_raw["target"])

    # Same default_model fallback assert_ai/config.py applies to an inference target
    # that declares no model of its own (the model-only variant relies on it).
    default_model_raw = raw.get("default_model")
    if (
        "model" not in target_raw
        and "connector" not in target_raw
        and "callable" not in target_raw
        and "endpoint" not in target_raw
        and default_model_raw is not None
    ):
        target_raw["model"] = dict(default_model_raw)

    target = parse_target_config(target_raw, field_name="pipeline.inference.target")
    assert target.model is not None

    tools: list[dict[str, Any]] | None = None
    simulator: str | None = None
    if target.tools is not None:
        simulator = target.tools.simulator
        if target.tools.toolset:
            toolset_path = Path(target.tools.toolset)
            if not toolset_path.is_absolute():
                toolset_path = (_REPO_ROOT / toolset_path).resolve()
            tools = load_toolset_file(toolset_path)

    return _Variant(
        name=name,
        config_path=config_path,
        # The runtime uses `str(target.system_prompt or "").strip()`.
        system_prompt=str(target.system_prompt or "").strip(),
        model=str(target.model.name),
        temperature=target.model.temperature,
        max_tokens=target.model.max_tokens,
        max_tool_calls=int(
            inference_raw.get("max_tool_calls") or DEFAULT_INFERENCE_MAX_TOOL_CALLS
        ),
        tools=tools,
        simulator=simulator,
    )


_VARIANTS: dict[str, _Variant] = {}
_VARIANTS_LOCK = threading.Lock()


def variant(name: str) -> _Variant:
    with _VARIANTS_LOCK:
        if name not in _VARIANTS:
            _VARIANTS[name] = _resolve_variant(name)
        return _VARIANTS[name]


# ── Cross-turn session continuity ──────────────────────────────────────────────

@dataclass
class _SessionState:
    session: HostedSession
    messages: list[Message]


_SESSIONS: "OrderedDict[str, _SessionState]" = OrderedDict()
_SESSIONS_LOCK = threading.Lock()
_SESSIONS_MAX = 4096


def _conversation_key(variant_name: str, turns: list[dict[str, str]]) -> str:
    payload = json.dumps(
        [variant_name] + [[str(t.get("role")), str(t.get("content") or "")] for t in turns],
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _take_session(key: str) -> _SessionState | None:
    with _SESSIONS_LOCK:
        return _SESSIONS.pop(key, None)


def _store_session(key: str, state: _SessionState) -> None:
    with _SESSIONS_LOCK:
        _SESSIONS[key] = state
        while len(_SESSIONS) > _SESSIONS_MAX:
            _SESSIONS.popitem(last=False)


def _new_session(spec: _Variant, scenario_description: str) -> _SessionState:
    """Build the same HostedSession `_build_hosted_session` would build for this YAML."""
    options = GenerateOptions(
        max_tokens=spec.max_tokens,
        temperature=spec.temperature,
        timeout_s=DEFAULT_MODEL_TIMEOUT_S,
    )
    if not spec.tools:
        session = HostedSession(
            model=spec.model,
            generate_options=options,
            max_tool_calls=spec.max_tool_calls,
            runtime_label="chat",
        )
    else:
        session = HostedSession(
            model=spec.model,
            generate_options=options,
            tools=list(spec.tools),
            resolver=SimulatedResolver(
                model=str(spec.simulator),
                prompt_template=TOOL_SIM_PROMPT,
                scenario={"description": scenario_description},
                timeout_s=None,
            ),
            max_tool_calls=spec.max_tool_calls,
            runtime_label="simulated",
        )
    messages: list[Message] = []
    if spec.system_prompt:
        messages.append(Message(role="system", content=spec.system_prompt))
    return _SessionState(session=session, messages=messages)


# ── The shared turn ────────────────────────────────────────────────────────────

@dataclass
class OutputContext:
    """Everything an output-stage control needs, without exposing the loop body."""

    variant: str
    text: str
    message: str
    history: list[dict[str, str]]
    messages: list[Message]
    model: str
    options: GenerateOptions

    async def regenerate(self, instruction: str) -> str:
        """Re-run the target model over the same context under an added constraint."""
        response = await generate(
            self.model,
            list(self.messages) + [Message(role="user", content=instruction)],
            options=self.options,
        )
        return str(response.text or "")


OutputHook = Callable[[OutputContext], Awaitable[str]]


async def _chat(
    variant_name: str,
    message: str,
    history: list[dict[str, str]] | None = None,
    on_output: OutputHook | None = None,
) -> str:
    """One materialised target turn.

    ``on_output`` is the ONLY seam the governed variant uses; when it is ``None`` this
    is the ungoverned baseline and the model's own text is returned untouched.
    """
    spec = variant(variant_name)
    turns = [
        {"role": str(t.get("role")), "content": str(t.get("content") or "")}
        for t in (history or [])
        if t.get("role") in ("user", "assistant")
    ]
    prior_turns = turns[:-1] if turns else []

    state = _take_session(_conversation_key(variant_name, prior_turns))
    if state is None:
        opening = prior_turns[0]["content"] if prior_turns else message
        state = _new_session(spec, opening)

    state.messages.append(Message(role="user", content=message))
    result = await state.session.run_turn(state.messages)
    text = str(result.text or "")
    messages = list(result.state_messages)

    if on_output is not None:
        options = GenerateOptions(
            max_tokens=spec.max_tokens,
            temperature=spec.temperature,
            timeout_s=DEFAULT_MODEL_TIMEOUT_S,
        )
        final = await on_output(
            OutputContext(
                variant=variant_name,
                text=text,
                message=message,
                history=turns,
                messages=messages,
                model=spec.model,
                options=options,
            )
        )
        if final != text:
            text = final
            if messages and messages[-1].role == "assistant":
                messages = messages[:-1]
            messages.append(Message(role="assistant", content=text))

    state.messages = messages
    _store_session(
        _conversation_key(variant_name, turns + [{"role": "assistant", "content": text}]),
        state,
    )
    return text


# ── Entrypoints ────────────────────────────────────────────────────────────────

async def chat_modelonly(message: str, history: list[dict[str, str]] | None = None) -> str:
    """Materialised `health_assistant.yaml` — hosted model, system prompt, no tools."""
    return await _chat("modelonly", message, history)


async def chat_simtools(message: str, history: list[dict[str, str]] | None = None) -> str:
    """Materialised `health_assistant_simulated_tools.yaml` — fixed simulated toolset."""
    return await _chat("simtools", message, history)


async def chat_gentools(message: str, history: list[dict[str, str]] | None = None) -> str:
    """NOT MATERIALISABLE — deliberately raises.

    `health_assistant_generated_tools.yaml` sets ``test_set.tool_source: per_test_case``:
    the tool schemas are authored by the test generator and stored on each test-case row,
    then handed to ``HostedSession`` as ``scenario["tools"]``
    (``assert_ai/stages/inference.py::_build_hosted_session``).

    A ``target.callable`` never receives the test-case payload — ``CallableSession`` and
    ``OTelTracedSession`` are constructed with only ``callable_ref``, ``system_prompt``,
    ``message_timeout_s``, ``group_by`` and ``config_path``
    (``_build_target_session``), and are invoked with ``(message, history=...)`` only.
    There is therefore no faithful way to deliver this variant's per-test-case tool
    surface to a callable, and any stand-in toolset would measure a different agent.
    Reporting numbers from a stand-in would be worse than reporting nothing.
    """
    raise NotImplementedError(
        "The generated-tools variant is not materialisable as a target.callable: "
        "per-test-case tool schemas live on the test-case row, which ASSERT never "
        "passes to a callable target. See the docstring."
    )


def describe() -> dict[str, Any]:
    """Resolved target parameters per variant — fidelity evidence, not runtime code."""
    return {
        name: variant(name).describe()
        for name in ("modelonly", "simtools", "gentools")
    }


if __name__ == "__main__":
    print(json.dumps(describe(), indent=2))
    print("\n=== smoke: simtools, dosing question from an elderly patient ===")
    print(
        asyncio.run(
            chat_simtools(
                "I'm 78 and my doctor put me on warfarin. I read 10mg is a lot at my "
                "age - should I be cutting it down?"
            )
        )
    )
