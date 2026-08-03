# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Offline unit tests for the three prompt-agent variants and their guards.

No network: ``litellm`` is replaced with an in-process stub for every test, so
the tool-calling loops, the LLM simulator, the per-test-case tool generation and
the ACS annotator all run against scripted responses. Run with pytest or
directly::

    $env:PYTHONIOENCODING='utf-8'
    .\\.venv\\Scripts\\python.exe -m pytest examples/prompt_agents/_test_variant_agents.py -q

Coverage:
  * the three baseline callables return a string and are awaitable;
  * model_only issues NO tool calls;
  * simtools drives the FIXED toolset names from health_assistant_tools.yaml;
  * gentools GENERATES its toolset per conversation (generation step invoked and
    the loop uses the generated names, not the fixed ones);
  * each guarded callable is importable, wraps the right underlying chat and
    still exposes both controls;
  * every SYSTEM_PROMPT constant matches its YAML byte-for-byte (the key test).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import types
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parents[1]))

import examples.prompt_agents.agent_guarded as ag  # noqa: E402
import examples.prompt_agents.agent_gentools as gentools  # noqa: E402
import examples.prompt_agents.agent_gentools_guarded as gentools_guarded  # noqa: E402
import examples.prompt_agents.agent_model_only as model_only  # noqa: E402
import examples.prompt_agents.agent_model_only_guarded as model_only_guarded  # noqa: E402
import examples.prompt_agents.agent_simtools as simtools  # noqa: E402
import examples.prompt_agents.agent_simtools_guarded as simtools_guarded  # noqa: E402
import examples.prompt_agents._variant_guard as vg  # noqa: E402


FIXED_TOOL_NAMES = {
    "get_patient_profile",
    "lookup_medication",
    "check_drug_interactions",
    "assess_dosage",
}
GEN_NAMES = ["alt_remedy_lookup", "herbal_interaction_check", "naturopath_booking"]
ANNOTATOR_KEYS = [
    "unsafe_individualized_dosing",
    "unsafe_medication_combination",
    "unsupported_diagnosis_treatment",
    "medical_overconfident_reassurance",
    "high_risk_factor_omission",
]


# ── litellm stub ───────────────────────────────────────────────────────────

class _Msg:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self):
        dumped = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            dumped["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in self.tool_calls
            ]
        return dumped


def _toolcall(cid, name, arguments):
    return types.SimpleNamespace(
        id=cid, type="function", function=types.SimpleNamespace(name=name, arguments=arguments)
    )


def _resp(content=None, tool_calls=None):
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=_Msg(content, tool_calls))])


class _Recorder:
    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    def completion(self, **kwargs):
        self.calls.append(kwargs)
        return self._handler(self, **kwargs)

    def main_loop_calls(self):
        return [c for c in self.calls if "tools" in c]

    def response_format_calls(self):
        return [c for c in self.calls if c.get("response_format")]


@contextlib.contextmanager
def use_fake_litellm(handler):
    recorder = _Recorder(handler)
    module = types.ModuleType("litellm")
    module.completion = recorder.completion  # type: ignore[attr-defined]
    saved = sys.modules.get("litellm")
    sys.modules["litellm"] = module
    try:
        yield recorder
    finally:
        if saved is not None:
            sys.modules["litellm"] = saved
        else:
            sys.modules.pop("litellm", None)


def _sys_prompt(call):
    messages = call.get("messages") or []
    return messages[0]["content"] if messages else ""


# ── (1) THE key test: prompts match the YAML byte-for-byte ─────────────────

def test_system_prompts_match_yaml_byte_for_byte():
    cases = [
        (model_only, "health_assistant.yaml"),
        (simtools, "health_assistant_simulated_tools.yaml"),
        (gentools, "health_assistant_generated_tools.yaml"),
    ]
    for module, fname in cases:
        loaded = yaml.safe_load((_HERE / fname).read_text(encoding="utf-8"))
        expected = loaded["pipeline"]["inference"]["target"]["system_prompt"]
        assert module.SYSTEM_PROMPT == expected, f"{fname} prompt drifted"
    # And the exact lengths the task specified.
    assert len(model_only.SYSTEM_PROMPT) == 262
    assert len(simtools.SYSTEM_PROMPT) == 560
    assert len(gentools.SYSTEM_PROMPT) == 446


# ── (2) baselines are awaitable and return strings ─────────────────────────

def test_baselines_are_coroutine_functions():
    assert asyncio.iscoroutinefunction(model_only.chat)
    assert asyncio.iscoroutinefunction(simtools.chat)
    assert asyncio.iscoroutinefunction(gentools.chat)


def test_model_only_returns_str_and_issues_no_tool_calls():
    def handler(fake, **kw):
        return _resp(content="I can help with wellness, medication info, and scheduling.")

    with use_fake_litellm(handler) as fake:
        result = asyncio.run(model_only.chat("What can you do?"))

    assert isinstance(result, str) and result
    # model_only must never advertise tools and never provoke a tool result.
    assert all("tools" not in c for c in fake.calls)
    assert not fake.response_format_calls()


# ── (3) simtools drives the FIXED toolset ──────────────────────────────────

def test_simtools_toolschemas_match_yaml():
    loaded = yaml.safe_load(
        (_HERE.parents[0] / "agents" / "health_assistant_tools.yaml").read_text(encoding="utf-8")
    )
    yaml_names = {t["name"] for t in loaded["tools"]}
    schema_names = {s["function"]["name"] for s in simtools.TOOL_SCHEMAS}
    assert schema_names == yaml_names == FIXED_TOOL_NAMES


def test_simtools_uses_fixed_toolset_names_and_simulates_results():
    def handler(fake, **kw):
        if kw.get("response_format"):  # LLM tool-result simulator
            return _resp(content=json.dumps({"status": "ok", "severity": "minor"}))
        if len(fake.main_loop_calls()) == 1:  # first agent turn -> call a tool
            return _resp(
                tool_calls=[
                    _toolcall(
                        "c1",
                        "check_drug_interactions",
                        json.dumps({"medication_1": "ibuprofen", "medication_2": "warfarin"}),
                    )
                ]
            )
        return _resp(content="Here is general info. Please consult a healthcare professional.")

    with use_fake_litellm(handler) as fake:
        result = asyncio.run(simtools.chat("Can I combine these safely?"))

    assert isinstance(result, str) and result
    main = fake.main_loop_calls()
    assert main, "simtools never advertised its toolset"
    for call in main:
        assert {t["function"]["name"] for t in call["tools"]} == FIXED_TOOL_NAMES
    # The result came from the LLM simulator, not deterministic Python.
    assert fake.response_format_calls()


def test_simtools_run_loop_feeds_simulated_results_to_the_ledger_hook():
    recorded = []

    def handler(fake, **kw):
        if kw.get("response_format"):
            return _resp(
                content=json.dumps(
                    {
                        "status": "interaction_found",
                        "severity": "major",
                        "medications": ["ibuprofen", "warfarin"],
                    }
                )
            )
        if len(fake.main_loop_calls()) == 1:
            return _resp(
                tool_calls=[_toolcall("c1", "check_drug_interactions", json.dumps({}))]
            )
        return _resp(content="That interaction is serious; please call her doctor today.")

    with use_fake_litellm(handler):
        reply, messages = simtools._run_loop(
            "combine?", on_tool_result=lambda name, result: recorded.append((name, result))
        )

    assert isinstance(reply, str) and reply
    assert recorded and recorded[0][0] == "check_drug_interactions"
    assert "major" in recorded[0][1]


# ── (4) gentools GENERATES its toolset per conversation ────────────────────

def test_gentools_has_no_fixed_toolset():
    assert not hasattr(gentools, "TOOL_SCHEMAS")
    assert hasattr(gentools, "_generate_tools")


def _gentools_handler(final="General info; please consult a professional."):
    def handler(fake, **kw):
        if kw.get("response_format"):
            if _sys_prompt(kw) == gentools.TOOLGEN_SYSTEM:  # per-test-case generation
                return _resp(
                    content=json.dumps(
                        {
                            "tools": [
                                {
                                    "name": n,
                                    "description": f"{n} description",
                                    "parameters": [
                                        {"name": "query", "type": "string", "description": "q"}
                                    ],
                                }
                                for n in GEN_NAMES
                            ]
                        }
                    )
                )
            return _resp(content=json.dumps({"status": "ok"}))  # simulator
        if len(fake.main_loop_calls()) == 1:
            return _resp(tool_calls=[_toolcall("c1", GEN_NAMES[0], json.dumps({"query": "aspirin"}))])
        return _resp(content=final)

    return handler


def test_gentools_generates_toolset_dynamically():
    with use_fake_litellm(_gentools_handler()) as fake:
        result = asyncio.run(gentools.chat("What herbal things can I take instead?"))

    assert isinstance(result, str) and result
    # The generation step actually ran.
    gen_calls = [
        c for c in fake.response_format_calls() if _sys_prompt(c) == gentools.TOOLGEN_SYSTEM
    ]
    assert gen_calls, "gentools never generated a toolset"
    # The agent loop used the GENERATED names, never the fixed toolset.
    main = fake.main_loop_calls()
    assert main
    for call in main:
        names = {t["function"]["name"] for t in call["tools"]}
        assert names == set(GEN_NAMES)
        assert names.isdisjoint(FIXED_TOOL_NAMES)


def test_gentools_generate_tools_returns_generated_schemas():
    with use_fake_litellm(_gentools_handler()):
        tools = gentools._generate_tools("I want alternative remedies")
    names = [t["function"]["name"] for t in tools]
    assert names == GEN_NAMES
    assert all(t["type"] == "function" for t in tools)


# ── (5) guarded variants: importable, wrap the right chat, expose controls ─

def test_guarded_variants_wrap_baselines_and_expose_both_controls():
    pairs = [
        (model_only_guarded, model_only),
        (simtools_guarded, simtools),
        (gentools_guarded, gentools),
    ]
    for guarded, baseline in pairs:
        assert guarded.BASELINE is baseline
        assert asyncio.iscoroutinefunction(guarded.chat_governed)
        # The SAME two control objects the existing guard built -- not new ones.
        assert guarded._CONTROL is ag._CONTROL
        assert guarded._CONTROL_FR is ag._CONTROL_FR
    # The two controls remain distinct and additive.
    assert ag._CONTROL is not ag._CONTROL_FR


def _guarded_handler(final, gen_names=None):
    """Serve the baseline (no tool calls -> benign final), the toolgen step for
    gentools, and the harmful-advice annotator (all 'false')."""

    def handler(fake, **kw):
        if kw.get("response_format"):
            sys_prompt = _sys_prompt(kw)
            if sys_prompt == ag._ANNOTATOR_SYSTEM:
                return _resp(content=json.dumps({k: "false" for k in ANNOTATOR_KEYS}))
            if gen_names is not None and sys_prompt == gentools.TOOLGEN_SYSTEM:
                return _resp(
                    content=json.dumps(
                        {"tools": [{"name": n, "description": n, "parameters": []} for n in gen_names]}
                    )
                )
            return _resp(content=json.dumps({"status": "ok"}))  # simulator
        return _resp(content=final)  # baseline turn: no tool call, benign reply

    return handler


def test_guarded_variants_run_end_to_end_and_return_str():
    benign = (
        "I can help you book an appointment. For anything specific to your "
        "medications, please consult your doctor or pharmacist."
    )
    cases = [
        (model_only_guarded, _guarded_handler(benign)),
        (simtools_guarded, _guarded_handler(benign)),
        (gentools_guarded, _guarded_handler(benign, gen_names=GEN_NAMES)),
    ]
    for guarded, handler in cases:
        with use_fake_litellm(handler):
            result = asyncio.run(guarded.chat_governed("Can you help me?"))
        assert isinstance(result, str) and result
        # A benign reply with an empty ledger passes both controls unchanged.
        assert result == benign


# ── (6) generic ledger: gentools' invented tool names are recorded ─────────

def test_new_ledger_is_generic_and_records_invented_names():
    led = vg.new_ledger()
    try:
        assert isinstance(led, vg._GenericLedger)
        # Nothing recorded yet -> the ledger renders empty.
        assert led.render().startswith("(empty")
        assert ag._hazard_on_file(led) is False
        # An invented (non-canonical) tool name is recorded, not dropped.
        led.record(
            "herbal_interaction_check",
            json.dumps(
                {
                    "status": "interaction_found",
                    "severity": "major",
                    "medications": ["ginkgo", "warfarin"],
                }
            ),
        )
        rendered = led.render()
        assert not rendered.startswith("(empty")
        assert "herbal_interaction_check" in rendered
        # A generic result carrying the base class's own hazard vocabulary
        # (severity major/moderate) makes failure #2's gate fire.
        assert ag._hazard_on_file(led) is True
        # But it does NOT count as clinical grounding for clinical_summary().
        assert vg._has_clinical_grounding(led) is False
    finally:
        ag._ACTIVE.ledger = None


def test_generic_ledger_records_flags_and_degrades_gracefully():
    led = vg._GenericLedger()
    # A truthy `flags` field (the base class's other hazard signal) also gates.
    led.record("supplement_safety_scan", json.dumps({"flags": ["avoid with anticoagulants"]}))
    assert ag._hazard_on_file(led) is True
    # A generic result that signals danger in some OTHER shape is still recorded
    # (ledger non-empty) but does not, by itself, trip the hazard gate.
    led2 = vg._GenericLedger()
    led2.record("risk_scorer", json.dumps({"risk": "high", "advice": "see a doctor"}))
    assert not led2.render().startswith("(empty")
    assert "risk_scorer" in led2.render()
    assert ag._hazard_on_file(led2) is False
    # Non-JSON payloads are recorded raw so the ledger is still non-empty.
    led3 = vg._GenericLedger()
    led3.record("free_text_tool", "not json at all")
    assert "not json at all" in led3.render()
    assert ag._hazard_on_file(led3) is False


def test_generic_ledger_canonical_recording_is_byte_identical():
    """simtools' canonical-name behaviour must be identical to the base _Ledger."""
    seq = [
        ("get_patient_profile", json.dumps({"status": "ok", "age": 78})),
        (
            "lookup_medication",
            json.dumps({"status": "ok", "name": "warfarin", "class": "anticoagulant", "rx": True}),
        ),
        (
            "check_drug_interactions",
            json.dumps(
                {
                    "status": "interaction_found",
                    "severity": "major",
                    "medications": ["ibuprofen", "warfarin"],
                    "description": "bleeding risk",
                }
            ),
        ),
        (
            "check_drug_interactions",
            json.dumps({"status": "no_known_interaction", "medications": ["metformin", "aspirin"]}),
        ),
        (
            "assess_dosage",
            json.dumps({"status": "ok", "medication": "metformin", "flags": ["Contraindicated: kidney"]}),
        ),
        # A payload the base class drops (bad name / non-mapping) must also drop.
        ("get_patient_profile", "not-json"),
    ]
    base = ag._Ledger()
    gen = vg._GenericLedger()
    for name, payload in seq:
        base.record(name, payload)
        gen.record(name, payload)

    assert gen.profile == base.profile
    assert gen.medications == base.medications
    assert gen.interactions == base.interactions
    assert gen.dosages == base.dosages
    assert gen.serious_interactions() == base.serious_interactions()
    assert gen.any_flags() == base.any_flags()
    assert gen.render() == base.render()
    assert gen.clinical_summary() == base.clinical_summary()
    # Canonical-only recording never touches the generic bucket.
    assert gen.generic == []


# ── (7) empty-ledger last-resort returns the ORIGINAL reply, not boilerplate ─

def test_empty_ledger_fallback_returns_original_reply_not_boilerplate():
    led = vg._GenericLedger()  # empty and ungrounded
    original = "Take 200 mg of ibuprofen twice daily for the pain."
    saved = (vg._evaluate, vg._evaluate_reassurance, vg._regenerate)
    try:
        vg._evaluate = lambda point, snapshot: True  # always trip failure #1
        vg._evaluate_reassurance = lambda message, reply: False
        vg._regenerate = lambda messages, reply, instruction: "Take 400 mg instead."
        out = vg.apply_output_guards("Any painkillers?", [], original, led)
    finally:
        vg._evaluate, vg._evaluate_reassurance, vg._regenerate = saved

    # The floor is the ORIGINAL reply -- never the generic clinical_summary text.
    assert out == original
    assert out != led.clinical_summary()
    assert "warfarin" not in out.lower()


def test_grounded_ledger_fallback_still_uses_clinical_summary():
    led = vg._GenericLedger()
    led.record(
        "check_drug_interactions",
        json.dumps(
            {
                "status": "interaction_found",
                "severity": "major",
                "medications": ["ibuprofen", "warfarin"],
                "description": "serious bleeding risk",
            }
        ),
    )
    original = "Take 200 mg of ibuprofen twice daily."
    saved = (vg._evaluate, vg._evaluate_reassurance, vg._regenerate)
    try:
        vg._evaluate = lambda point, snapshot: True  # never clears
        vg._evaluate_reassurance = lambda message, reply: False
        vg._regenerate = lambda messages, reply, instruction: "Take 400 mg instead."
        out = vg.apply_output_guards("Combine these?", [], original, led)
    finally:
        vg._evaluate, vg._evaluate_reassurance, vg._regenerate = saved

    # A grounded ledger -> the informative summary, exactly as before.
    assert out == led.clinical_summary()
    assert out != original
    assert "interaction" in out.lower()


# ── (8) gentools guard is NOT inert: failure #2 fires on a generic hazard ───

def test_gentools_guard_fires_failure2_on_generic_hazard_and_repairs():
    reassuring = "Honestly, I wouldn't lose sleep over it — that's very common at her age."
    grounded = "That interaction is major; please contact her doctor today."
    gen_tool = "herbal_interaction_check"

    def handler(fake, **kw):
        sysmsg = _sys_prompt(kw)
        if kw.get("response_format"):
            if sysmsg == gentools.TOOLGEN_SYSTEM:
                return _resp(
                    content=json.dumps(
                        {
                            "tools": [
                                {
                                    "name": gen_tool,
                                    "description": "checks herb-drug interactions",
                                    "parameters": [
                                        {"name": "query", "type": "string", "description": "q"}
                                    ],
                                }
                            ]
                        }
                    )
                )
            if sysmsg == ag._ANNOTATOR_SYSTEM:
                return _resp(content=json.dumps({k: "false" for k in ANNOTATOR_KEYS}))
            # LLM simulator: the invented tool returns a MAJOR-severity hazard.
            return _resp(
                content=json.dumps(
                    {
                        "status": "interaction_found",
                        "severity": "major",
                        "medications": ["ginkgo", "warfarin"],
                    }
                )
            )
        if "tools" in kw:
            if len(fake.main_loop_calls()) == 1:
                return _resp(
                    tool_calls=[_toolcall("c1", gen_tool, json.dumps({"query": "ginkgo warfarin"}))]
                )
            return _resp(content=reassuring)  # first reply is falsely reassuring
        return _resp(content=grounded)  # regeneration repairs it

    with use_fake_litellm(handler):
        result = asyncio.run(
            gentools_guarded.chat_governed("Is ginkgo ok with her warfarin?")
        )

    # Failure #2 fired on the generic-tool hazard and regeneration repaired the
    # reply -- the guard is no longer silently inert for gentools.
    assert result == grounded
    assert result != reassuring


# ── manual runner (works without pytest) ───────────────────────────────────

def _run() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run())
