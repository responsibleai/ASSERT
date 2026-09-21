"""Generalization tests for the sensitivity-tier authorization rule.

These execute the REAL artifacts:

* ``acs/policy_tier_authz/tier_authorization.rego`` through the ``opa`` binary
* ``runtime/deposit_tier_gate.py`` (the realistic baseline) as plain Python

Neither artifact is modified, monkeypatched, or re-implemented here. The two
hypothetical domains (``insurance_policy``, ``trust_account``) exist nowhere in
the product code. Their normalized envelopes are submitted directly to policy
evaluation, so these tests prove the Rego predicate but do not exercise an
end-to-end host or wrapper path for those domains.

Run::

    pytest examples/bank_manager_agent_control/tests/test_tier_authorization_generalization.py -v
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXAMPLE_ROOT / "scripts"))
sys.path.insert(0, str(EXAMPLE_ROOT / "runtime"))

import generalization_proof as proof  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("opa") is None, reason="requires the opa binary on PATH"
)

LAUNCH_DOMAIN = "deposit_account"
HELD_OUT_DOMAINS = {"insurance_policy", "trust_account"}


@pytest.fixture(scope="module")
def records() -> list[dict]:
    rows = proof.all_records()
    assert rows, "fixture produced no records"
    return rows


def _split(records: list[dict]) -> tuple[list[dict], list[dict]]:
    return (
        [r for r in records if r["risk_tier"] in ("high_net_worth", "vip", "restricted")],
        [r for r in records if r["risk_tier"] not in ("high_net_worth", "vip", "restricted")],
    )


def test_fixture_spans_launch_and_held_out_domains(records: list[dict]) -> None:
    domains = {proof._domain(r["entity_id"]) for r in records}
    assert LAUNCH_DOMAIN in domains
    assert HELD_OUT_DOMAINS <= domains, f"held-out domains missing from fixture: {domains}"


def test_rego_denies_every_protected_record_in_every_domain(records: list[dict]) -> None:
    """The unmodified property rule handles all six normalized input sets."""
    protected, _ = _split(records)
    missed = [r["entity_id"] for r in protected if not proof.rego_denies(r)]
    assert missed == [], f"rego failed to protect: {missed}"


def test_rego_has_no_false_positives_on_standard_records(records: list[dict]) -> None:
    """A control plane that blocks everything is useless. Standard tier stays open."""
    _, standard = _split(records)
    over = [r["entity_id"] for r in standard if proof.rego_denies(r)]
    assert over == [], f"rego over-blocked standard records: {over}"


def test_rego_allows_protected_records_once_authorization_exists(records: list[dict]) -> None:
    """It is a gate, not a ban: with a verified approval the same read is allowed."""
    protected, _ = _split(records)
    still_denied = [
        r["entity_id"]
        for r in protected
        if proof.rego_denies(r, authorized=[r["entity_id"]])
    ]
    assert still_denied == [], f"rego denied despite authorization: {still_denied}"


def test_rego_authorization_is_keyed_per_entity(records: list[dict]) -> None:
    """Authorization for one record must not unlock a different one."""
    protected, _ = _split(records)
    assert len(protected) >= 2
    subject, other = protected[0], protected[1]
    assert proof.rego_denies(subject, authorized=[other["entity_id"]]), (
        "authorization for a different entity was accepted"
    )


def test_baseline_covers_its_launch_domain(records: list[dict]) -> None:
    """The baseline is competent WHERE IT WAS WIRED. This is why it is realistic."""
    protected, _ = _split(records)
    launch = [r for r in protected if proof._domain(r["entity_id"]) == LAUNCH_DOMAIN]
    assert launch, "no protected deposit accounts in fixture"
    missed = [r["entity_id"] for r in launch if not proof.baseline_denies(r)]
    assert missed == [], f"baseline missed its own domain: {missed}"


def test_baseline_covers_new_records_in_its_launch_domain() -> None:
    """It keys on the tier property, not an ID allow-list, so a brand-new VIP
    deposit account added after it was written is still covered."""
    unseen = {
        "entity_id": "ACC-9999",
        "domain": LAUNCH_DOMAIN,
        "tool": "read_account",
        "risk_tier": "vip",
        "account_sensitivity": "vip",
    }
    assert proof.baseline_denies(unseen)
    assert proof.rego_denies(unseen)


def test_baseline_does_not_generalize_to_other_domains(records: list[dict]) -> None:
    """The whole finding. Not 'the baseline is broken' — 'the baseline stops at
    the edge of the service it was written in.'"""
    protected, _ = _split(records)
    other = [r for r in protected if proof._domain(r["entity_id"]) != LAUNCH_DOMAIN]
    assert other, "fixture has no non-deposit protected records"
    covered = [r["entity_id"] for r in other if proof.baseline_denies(r)]
    assert covered == [], (
        "baseline unexpectedly covered a non-deposit domain; the generalization "
        f"contrast no longer holds: {covered}"
    )


def test_baseline_has_no_false_positives(records: list[dict]) -> None:
    _, standard = _split(records)
    over = [r["entity_id"] for r in standard if proof.baseline_denies(r)]
    assert over == [], f"baseline over-blocked: {over}"


def test_headline_coverage_numbers(records: list[dict]) -> None:
    """Pins the numbers quoted in the write-up so they cannot silently drift."""
    protected, standard = _split(records)
    rego_tp = sum(proof.rego_denies(r) for r in protected)
    base_tp = sum(proof.baseline_denies(r) for r in protected)
    assert (rego_tp, len(protected)) == (13, 13)
    assert (base_tp, len(protected)) == (2, 13)
    assert len(standard) == 11
    assert sum(proof.rego_denies(r) for r in standard) == 0
    assert sum(proof.baseline_denies(r) for r in standard) == 0


def test_rego_source_contains_no_domain_specific_identifiers() -> None:
    """Static check that the rule really is property-based. If someone 'fixes'
    coverage by pasting in an ID prefix or a tool name, this fails."""
    source = (EXAMPLE_ROOT / "acs" / "policy_tier_authz" / "tier_authorization.rego").read_text(
        encoding="utf-8"
    )
    body = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    banned = [
        "ACC-", "CL-", "LN-", "BR-", "INS-", "TR-",
        "read_account", "read_loan", "read_client", "read_portfolio",
        "account_sensitivity", "deposit", "loan", "brokerage",
    ]
    found = [token for token in banned if token in body]
    assert found == [], f"rego leaked domain-specific identifiers: {found}"
