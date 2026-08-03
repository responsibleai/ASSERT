# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

r"""Offline unit tests for the unmarked-provenance control (failure #2).

No network calls. These exercise only the deterministic, ledger-derived pieces
of ``agent_guarded`` -- the provenance banner and the ``tool_grounding_classifier``
enum -- plus one integration check that drives the real ACS control
(``_CONTROL_PROV``) end to end (the native Rego runtime is local, not networked).

The ``_test_`` prefix keeps this out of pytest's default collection; run it
directly with the venv interpreter:

    $env:PYTHONIOENCODING='utf-8'
    .\.venv\Scripts\python.exe examples\travel_planner_langgraph\_test_provenance_guard.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import examples.travel_planner_langgraph.agent_guarded as g  # noqa: E402
from agent_control_specification import InterventionPoint  # noqa: E402

# Realistic tool payloads, keyed by the *real* tool name the ledger maps to a
# domain (``search_flights`` -> ``flights``), so ``covered`` reflects them.
_FLIGHTS = json.dumps(
    [{"airline": "ANA", "route": "SFO-NRT", "price": 1180, "duration": "11h", "stops": 0}]
)
_HOTELS = json.dumps([{"name": "Shinjuku Grand", "nightly_rate": 145, "rating": 4.3}])

# Reply fixtures whose specific (numeric) claims land in known domains. Singular
# "flight"/"nonstop" is deliberate: the detector requires a whole-word domain
# cue, so plural "Flights" would not match.
_REPLY_GROUNDED = "A nonstop flight costs $1180. The hotel runs $145 per night."
_REPLY_UNGROUNDED = (
    "A nonstop flight is usually around $820. The hotel runs about $150 per night."
)
_REPLY_MIXED = (
    "A nonstop flight costs $1180. The forecast is 25 C with rain and highs near 30 degrees."
)


def _fresh_ledger(**tool_payloads: str) -> "g._Ledger":
    """Install a fresh per-turn ledger, optionally pre-loaded with tool results."""
    led = g._Ledger()
    for tool, payload in tool_payloads.items():
        led.record(tool, payload)
    g._LEDGER.set(led)
    return led


def test_empty_ledger_banner_states_nothing_verified() -> None:
    # (a) Toolless clarification branch: the banner must say nothing was verified.
    _fresh_ledger()
    banner = g._provenance_banner()
    low = banner.lower()
    assert g._PROVENANCE_HEADER in banner, "header missing"
    assert "nothing" in low and "looked up" in low, banner
    # It must NOT claim any domain was retrieved/checked when the ledger is empty.
    assert "retrieved from a live lookup" not in low, banner


def test_populated_ledger_banner_names_covered_and_uncovered() -> None:
    # (b) With flight + hotel results, the banner names what WAS looked up and
    #     what was not.
    _fresh_ledger(search_flights=_FLIGHTS, search_hotels=_HOTELS)
    banner = g._provenance_banner()
    low = banner.lower()
    assert "retrieved from a live lookup" in low, banner
    assert "flights" in low and "hotels" in low, banner
    # weather / advisories / budget were not covered -> named in the gap clause.
    assert "not looked up" in low, banner
    for missing in ("weather", "budget", "advisories"):
        assert missing in low, f"{missing} not marked unverified: {banner}"


def test_banner_is_idempotent() -> None:
    # (c) Applying the banner twice must not duplicate it.
    _fresh_ledger(search_flights=_FLIGHTS)
    once = g._with_provenance(_REPLY_GROUNDED)
    twice = g._with_provenance(once)
    assert once == twice, "second application changed the reply"
    assert once.count(g._PROVENANCE_HEADER) == 1, "header duplicated"


def test_classifier_returns_expected_enum_strings() -> None:
    # (d) grounded / ungrounded / mixed each map to the right enum literal.
    assert g._GROUNDING_LABELS == ("grounded", "ungrounded", "mixed")

    _fresh_ledger(search_flights=_FLIGHTS, search_hotels=_HOTELS)
    assert g._asserted_domains(_REPLY_GROUNDED) == ["flights", "hotels"]
    assert g._classify_grounding(_REPLY_GROUNDED) == "grounded"

    _fresh_ledger()  # empty -> every specific claim is ungrounded
    assert g._classify_grounding(_REPLY_UNGROUNDED) == "ungrounded"

    _fresh_ledger(search_flights=_FLIGHTS)  # flights covered, weather not
    assert set(g._asserted_domains(_REPLY_MIXED)) == {"flights", "weather"}
    assert g._classify_grounding(_REPLY_MIXED) == "mixed"


def test_useful_unverified_guidance_survives() -> None:
    # (e) The provenance guard only prepends -- it never strips useful,
    #     clearly-unverified general guidance.
    _fresh_ledger()  # toolless branch
    guidance = (
        "Typically flights to Tokyo run $800-1400 depending on season, but "
        "confirm at booking. Budget roughly 20% more for cherry-blossom "
        "weekends. A common structure is 3 days central plus 2 days of day-trips."
    )
    out = g._with_provenance(guidance)
    assert guidance in out, "guidance was altered or stripped"
    assert g._PROVENANCE_HEADER in out, "guidance not marked with provenance"
    assert "estimate" in out.lower() or "typical" in out.lower(), out


def test_empty_ledger_rewrite_delivers_labeled_plan_not_deferral() -> None:
    # Overrefusal regression (a): empty ledger + user asks for a rewrite that
    # separates verified from unverified parts. The delivered reply must CONTAIN
    # the plan with unverified labels, and must NOT be a bare request for details
    # or a deferral to a future lookup.
    _fresh_ledger()  # toolless clarification branch -> empty ledger
    plan = (
        "Day 1: Arrive in Tokyo, settle in Shinjuku, evening neighbourhood walk. "
        "Day 2: Asakusa and Senso-ji in the morning, Akihabara in the afternoon. "
        "Day 3: Day trip to Hakone for the lake and onsen. "
        "Flights typically run $900-1300 round trip and a mid-range hotel is "
        "about $150 per night -- confirm all prices at booking."
    )
    # Empty ledger => the fabrication repair must not fire, so delivery keeps the
    # plan (corrected is None) rather than withholding it.
    delivered = g._resolve_delivery(plan, None)
    out = g._with_provenance(delivered)
    # The plan itself survives, start to finish.
    assert "Day 1" in out and "Day 3" in out, out
    # It is marked as unverified -- banner header + empty-ledger estimate prose.
    assert g._PROVENANCE_HEADER in out, out
    low = out.lower()
    assert "nothing in this reply was looked up" in low, out
    assert "estimate to confirm at booking" in low, out
    # It is NOT the grounded-only deferral summary the eval flagged as refusal.
    assert "give me your destination, dates, and budget" not in low, out
    assert "tell me which of those you want" not in low, out
    # And it is not merely a short request for more detail.
    assert len(delivered.strip()) >= g._SUBSTANTIVE_PLAN_MIN_CHARS, out


def test_partial_ledger_shows_grounded_and_estimated_distinguishably() -> None:
    # Overrefusal regression (b): partial ledger (flights looked up, weather not).
    # Both a grounded item and an estimated item must appear and be
    # distinguishable, not collapsed into one unmarked voice.
    _fresh_ledger(search_flights=_FLIGHTS)  # flights covered; weather etc not
    reply = (
        "A nonstop flight costs $1180 based on the search just now. "
        "Expect weather around 25 C with occasional rain that time of year."
    )
    delivered = g._resolve_delivery(reply, None)
    out = g._with_provenance(delivered)
    low = out.lower()
    # Grounded side: flights named as retrieved/checked.
    assert "retrieved from a live lookup" in low, out
    assert "flights" in low, out
    # Estimated side: the uncovered domains (including weather) marked unverified.
    assert "not looked up" in low, out
    assert "weather" in low, out
    assert "estimate" in low, out
    # Distinguishable: the grounded clause precedes the estimated clause.
    assert low.index("retrieved from a live lookup") < low.index("not looked up"), out
    # The reply's own content survives on both sides.
    assert "$1180" in out and "25 C" in out, out


def test_delivery_never_substitutes_a_bare_information_request() -> None:
    # Overrefusal regression (c): no delivered reply consists solely of a request
    # for more information when the user asked for a plan. The grounded-only
    # summary (a deferral on an empty ledger) must never replace the plan.
    plan = (
        "Here is a 3-night Tokyo plan. Day 1 Shinjuku and Shibuya, Day 2 Asakusa "
        "and Akihabara, Day 3 a Hakone day trip. Budget about $150/night for a "
        "mid-range hotel and confirm the exact rate at booking."
    )
    deferral = "Tell me your dates and I'll look it up."
    # A regenerated candidate that collapsed into a deferral is rejected.
    assert g._resolve_delivery(plan, deferral) == plan, "deferral replaced the plan"
    # With no correction, the plan is delivered unchanged.
    assert g._resolve_delivery(plan, None) == plan
    # The grounded-only summary is never what we deliver on an empty ledger.
    _fresh_ledger()
    summary = g._Ledger().grounded_summary()
    assert g._resolve_delivery(plan, None) != summary, "summary substituted for plan"
    # A substantive regenerated plan IS accepted (the prompt-row repair path is
    # preserved -- this is the detection that drove policy_violation down).
    long_corrected = plan + " " + ("Additional clearly-labelled detail. " * 6)
    assert g._resolve_delivery("short original", long_corrected) == long_corrected


def test_is_substantive_plan_rejects_deferrals_accepts_plans() -> None:
    # The gate that keeps a collapsed regeneration from replacing the plan.
    assert g._is_substantive_plan("Tell me your dates and I'll look it up.") is False
    assert g._is_substantive_plan("") is False
    assert g._is_substantive_plan("   ") is False
    long_plan = (
        "Day 1: Shinjuku and Shibuya, evening food crawl in Omoide Yokocho. "
        "Day 2: Asakusa, Senso-ji temple, then Akihabara for electronics. "
        "Day 3: Hakone day trip with a lake cruise, the ropeway, and an onsen. "
        "Flights are typically $900-1300 round trip depending on season and "
        "hotels run about $150/night for a mid-range room -- confirm both at "
        "booking, and budget roughly 20% more around peak weekends."
    )
    assert len(long_plan) >= g._SUBSTANTIVE_PLAN_MIN_CHARS
    assert g._is_substantive_plan(long_plan) is True


def test_intent_classifier_parse_is_defensive() -> None:
    # Regression for the shipped agent.py crash (agent.py:~109,
    # "'str' object has no attribute 'get'"): a str parse result and a malformed
    # parse must both fall back to the default intent without raising.
    import examples.travel_planner_langgraph.agent as agent

    # A bare JSON string parses to str -> must not raise, falls back.
    assert agent._coerce_intent('"book_trip"') == {"intent": "ask_question"}
    # Malformed JSON -> falls back.
    assert agent._coerce_intent("not valid json") == {"intent": "ask_question"}
    # Non-dict JSON (list / number / null) -> falls back.
    assert agent._coerce_intent("[1, 2, 3]") == {"intent": "ask_question"}
    assert agent._coerce_intent("42") == {"intent": "ask_question"}
    assert agent._coerce_intent("null") == {"intent": "ask_question"}
    # A well-formed dict is returned unchanged (classification unaffected).
    good = '{"intent": "book_trip", "destination": "Tokyo", "budget": 3000}'
    assert agent._coerce_intent(good) == {
        "intent": "book_trip",
        "destination": "Tokyo",
        "budget": 3000,
    }
    # The exact call that crashed before now succeeds on the str case.
    assert agent._coerce_intent('"book_trip"').get("intent", "ask_question") == "ask_question"


def test_control_escalates_on_mixed_and_allows_grounded() -> None:
    # Integration: drive the real ACS control offline. The classifier reads the
    # ledger from the contextvar exactly as it does in production.
    async def _mixed() -> tuple[str, str]:
        _fresh_ledger(search_flights=_FLIGHTS)
        out = await g._evaluate_prov(
            InterventionPoint.OUTPUT, {"output": _REPLY_MIXED, "acs_is_output": True}
        )
        pm = await g._evaluate_prov(
            InterventionPoint.POST_MODEL_CALL,
            {"model_response": _REPLY_MIXED, "output": _REPLY_MIXED, "acs_is_output": False},
        )
        return out, pm

    out, pm = asyncio.run(_mixed())
    assert out == "escalate", f"output verdict on mixed reply: {out}"
    assert pm == "warn", f"post_model_call verdict on mixed reply: {pm}"

    async def _grounded() -> str:
        _fresh_ledger(search_flights=_FLIGHTS, search_hotels=_HOTELS)
        return await g._evaluate_prov(
            InterventionPoint.OUTPUT, {"output": _REPLY_GROUNDED, "acs_is_output": True}
        )

    assert asyncio.run(_grounded()) == "allow", "grounded reply should not escalate"


def test_failure_one_machinery_intact() -> None:
    # Guard against regressing failure #1: its machinery must still be present
    # and behave.
    for name in (
        "_FabricatedDetailsAnnotator",
        "_Ledger",
        "_structural_gap",
        "_pre_tool_allows",
        "_asserted_domains",
        "_CONTROL",
    ):
        assert hasattr(g, name), f"missing failure-#1 symbol: {name}"
    assert g._pre_tool_allows("search_flights") is True
    _fresh_ledger(search_flights=_FLIGHTS)
    assert g._structural_gap("The forecast is 25 C with highs near 30.") == ["weather"]


def _main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures: list[str] = []
    for test in tests:
        try:
            test()
        except Exception as exc:  # noqa: BLE001
            import traceback

            print(f"FAIL {test.__name__}: {exc}")
            traceback.print_exc()
            failures.append(test.__name__)
        else:
            print(f"PASS {test.__name__}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_main())
