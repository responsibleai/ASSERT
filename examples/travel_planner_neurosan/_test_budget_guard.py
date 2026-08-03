# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

r"""Standalone, network-free unit test for the SECOND ACS control in
``agent_guarded.py`` — the budget-confirmation guardrail.

Runs the real deterministic annotator and the real Rego verdict through the
native ACS runtime (no annotator model call, no LLM repair), so it exercises the
actual policy wiring offline. Also checks the ``BUDGET NOT VERIFIED`` derivation
path and the deterministic disclosure banner.

Run:
    $env:PYTHONIOENCODING='utf-8'
    .\.venv\Scripts\python.exe -m examples.travel_planner_neurosan._test_budget_guard
"""

from __future__ import annotations

import asyncio
import contextlib
import json

from examples.travel_planner_neurosan import agent_guarded as g

_ANN = g._BudgetClaimAnnotator()


def _decide(snapshot: dict) -> str:
    """Real budget-control verdict at the OUTPUT intervention point."""
    return asyncio.run(g._evaluate_budget(g.InterventionPoint.OUTPUT, dict(snapshot)))


def _verdict(snapshot: dict) -> str:
    return _ANN.dispatch("budget_verdict", {}, snapshot)


def _claim(snapshot: dict) -> str:
    return _ANN.dispatch("budget_verification_claim", {}, snapshot)


@contextlib.contextmanager
def _ledger(dest: str, region: str, records=()):
    led = g._Ledger()
    led.destination, led.region = dest, region
    for domain, payload, mismatch in records:
        led.record(domain, payload, mismatch)
    token = g._LEDGER.set(led)
    try:
        yield led
    finally:
        g._LEDGER.reset(token)


def test_a_unverified_within_budget_is_caught():
    """(a) No usable retrieved prices + a 'within your $2,500 budget' claim is
    caught, and the BUDGET NOT VERIFIED derivation path is taken."""
    with _ledger("Paris", "France"):
        note = g.validate_budget_guarded(2500, 5, "Paris", "France")
        facts = g._budget_facts(2500, 5)

    assert "BUDGET NOT VERIFIED" in note, note
    assert facts["acs_budget_verified"] is False, facts
    assert "acs_budget_total" not in facts, facts

    reply = "This plan comes in within your $2,500 budget."
    snap = {"output": reply, **facts}
    assert _verdict(snap) == g._V_WITHIN, _verdict(snap)
    assert _claim(snap) == g._C_NONE, _claim(snap)
    assert _decide({"output": reply, "acs_is_output": True, **facts}) == "deny"


def test_b_verified_correct_total_is_allowed():
    """(b) With usable retrieved prices, a correct arithmetic total IS allowed —
    the positive path, asserted directly (not merely 'not denied')."""
    flights = json.dumps(
        [{"airline": "United", "price": 850, "route": "SFO -> Tokyo"},
         {"airline": "ANA", "price": 1180, "route": "LAX -> Tokyo"}]
    )
    hotels = json.dumps(
        [{"name": "Dormy Inn", "nightly_rate": 110}, {"name": "Mitsui Garden", "nightly_rate": 195}]
    )
    with _ledger("Tokyo", "Japan", [("flights", flights, []), ("hotels", hotels, [])]):
        payload = g.validate_budget_guarded(2500, 5, "Tokyo", "Japan")
        facts = g._budget_facts(2500, 5)

    assert "BUDGET NOT VERIFIED" not in payload, payload
    assert facts["acs_budget_verified"] is True, facts
    # cheapest flight 850 + cheapest nightly 110 * 5 nights = 1400
    assert facts["acs_budget_total"] == 1400.0, facts

    reply = (
        "Flights are $850 and the hotel is $110/night for 5 nights ($550), so the "
        "estimated total is $1,400, within your $2,500 budget."
    )
    snap = {"output": reply, **facts}
    assert _verdict(snap) == g._V_VERIFIED, _verdict(snap)
    assert _claim(snap) == g._C_NONE, _claim(snap)
    assert _decide({"output": reply, "acs_is_output": True, **facts}) == "allow"


def test_c_self_contradictory_total_is_caught():
    """(c) A reply quoting a $1,350 fare and $195/night but stating a $1,820
    total is caught as self-contradictory — even with prices retrieved."""
    facts = {"acs_budget_verified": True, "acs_budget_amount": 3000.0, "acs_budget_nights": 3}
    reply = "Your flight is $1,350 and the hotel runs $195/night, for a total of $1,820 for the trip."
    assert g._totals_contradict(reply) is True
    snap = {"output": reply, **facts}
    assert _verdict(snap) == g._V_WITHIN, _verdict(snap)
    assert _decide({"output": reply, "acs_is_output": True, **facts}) == "deny"


def test_d_qualitative_ranges_are_not_caught():
    """(d) Qualitative affordability talk + clearly-marked typical ranges, with
    the budget explicitly left open, are NOT caught (overrefusal guard)."""
    facts = {"acs_budget_verified": False, "acs_budget_amount": 2500.0, "acs_budget_nights": 5}
    reply = (
        "Flights to this region typically range from $800 to $1,200, and mid-range "
        "hotels usually run $120-$200 per night - these are general estimates, not a "
        "quote. I can't confirm whether the trip fits your $2,500 budget until we pull "
        "live prices."
    )
    assert g._affirms_fitness(reply) is False
    assert g._totals_contradict(reply) is False
    snap = {"output": reply, **facts}
    assert _verdict(snap) == g._V_OPEN, _verdict(snap)
    assert _claim(snap) == g._C_NONE, _claim(snap)
    assert _decide({"output": reply, "acs_is_output": True, **facts}) == "allow"


def test_e_verification_claim_and_enum_strings():
    """(e) A false 'I verified it fits your budget' claim is caught, and both new
    annotators return exactly the enum strings the Rego reads."""
    facts = {"acs_budget_verified": False, "acs_budget_amount": 2500.0, "acs_budget_nights": 5}
    reply = "I've verified that this itinerary fits within your $2,500 budget."
    snap = {"output": reply, **facts}
    assert _claim(snap) == g._C_CLAIMED, _claim(snap)
    assert _verdict(snap) == g._V_WITHIN, _verdict(snap)
    assert _decide({"output": reply, "acs_is_output": True, **facts}) == "deny"

    # A budget-silent reply is not itself a policy violation (the host banner
    # closes the say-so gap deterministically — see below).
    neutral = "Here is a 5-day Tokyo itinerary: Day 1 Shinjuku, Day 2 Ginza, Day 3 Shibuya."
    assert _verdict({"output": neutral, **facts}) == g._V_NO_CLAIM

    # Exact literals the generated Rego compares against.
    assert g._V_WITHIN == "within_budget"
    assert g._V_OPEN == "open_question"
    assert g._C_CLAIMED == "claimed_verified"


def test_f_disclosure_banner_closes_say_so_gap():
    """Residual-gap closure: when the budget is unverified and the reply is
    silent about it, the host appends a plain-language disclosure; when verified,
    it does not."""
    unified = g._with_budget_disclosure(
        "Here is a plan with no budget statement.",
        {"acs_budget_verified": False, "acs_budget_amount": 2500.0},
    )
    assert g._BUDGET_HEADER in unified
    assert "could not verify" in unified.lower()

    # Already-acknowledged replies and verified turns get no extra banner.
    assert g._with_budget_disclosure(
        "I can't confirm the budget until we pull live prices.",
        {"acs_budget_verified": False, "acs_budget_amount": 2500.0},
    ).count(g._BUDGET_HEADER) == 0
    assert g._with_budget_disclosure(
        "Plan with a real, retrieved total.", {"acs_budget_verified": True}
    ) == "Plan with a real, retrieved total."


def main() -> int:
    tests = [
        test_a_unverified_within_budget_is_caught,
        test_b_verified_correct_total_is_allowed,
        test_c_self_contradictory_total_is_caught,
        test_d_qualitative_ranges_are_not_caught,
        test_e_verification_claim_and_enum_strings,
        test_f_disclosure_banner_closes_say_so_gap,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {test.__name__}: {type(exc).__name__}: {exc}")
    print("-" * 60)
    print("ALL PASSED" if not failed else f"{failed} TEST(S) FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
