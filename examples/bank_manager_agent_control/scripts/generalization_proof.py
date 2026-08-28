"""Direct property-policy exercise for the sensitivity-tier authorization rule.

Runs the **unmodified** Rego rule
(``acs/policy_tier_authz/tier_authorization.rego``) and the **unmodified**
realistic baseline check (``runtime/deposit_tier_gate.py``) against normalized
result envelopes for every protected record in the bank, plus two hypothetical
domains that did not exist when either was written.

Neither artifact is edited, patched, or parameterised for this script. The only
thing that changes is the input. The hypothetical rows are submitted directly
to policy evaluation; this script does not exercise host lookup, tool
registration, or wrapper execution for those domains.

Usage::

    python examples/bank_manager_agent_control/scripts/generalization_proof.py
    python .../generalization_proof.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

EXAMPLE_DIR = Path(__file__).resolve().parents[1]
RUNTIME_DIR = EXAMPLE_DIR / "runtime"
BUNDLE = EXAMPLE_DIR / "acs" / "policy_tier_authz"
QUERY = "data.agent_control_specification.tier_authorization.post_tool_call_verdict"

sys.path.insert(0, str(RUNTIME_DIR))

import bank_core as core  # noqa: E402
import deposit_tier_gate  # noqa: E402

OPA = shutil.which("opa") or str(Path.home() / ".local" / "bin" / "opa")

# ---------------------------------------------------------------------------
# Domains that shipped WITH the deposit gate, and domains that shipped after.
# ---------------------------------------------------------------------------
LAUNCH_DOMAIN = "deposit_account"

# Two hypothetical domains. They exist nowhere in bank_core, the Rego, or the
# baseline gate and are exercised only as direct normalized policy inputs.
FUTURE_DOMAINS: dict[str, list[dict]] = {
    "insurance_policy": [
        {"entity_id": "INS-5001", "risk_tier": "standard", "tool": "read_policy",
         "annual_premium": 1450.0, "coverage": 250000},
        {"entity_id": "INS-5002", "risk_tier": "vip", "tool": "read_policy",
         "annual_premium": 96400.0, "coverage": 42000000},
        {"entity_id": "INS-5003", "risk_tier": "high_net_worth", "tool": "read_policy",
         "annual_premium": 21800.0, "coverage": 6500000},
        {"entity_id": "INS-5004", "risk_tier": "restricted", "tool": "surrender_policy",
         "surrender_value": 1880000.0},
    ],
    "trust_account": [
        {"entity_id": "TR-6001", "risk_tier": "standard", "tool": "read_trust",
         "corpus": 84000.0, "beneficiaries": 2},
        {"entity_id": "TR-6002", "risk_tier": "restricted", "tool": "read_trust",
         "corpus": 71500000.0, "beneficiaries": 9, "settlor_pep": True},
        {"entity_id": "TR-6003", "risk_tier": "vip", "tool": "distribute_corpus",
         "amount": 3400000.0},
        {"entity_id": "TR-6004", "risk_tier": "standard", "tool": "read_trust",
         "corpus": 12000.0, "beneficiaries": 1},
    ],
}

DOMAIN_OF_PREFIX = {
    "ACC": "deposit_account",
    "CL": "client_record",
    "LN": "loan",
    "BR": "brokerage",
    "INS": "insurance_policy",
    "TR": "trust_account",
}


def _existing_records() -> list[dict]:
    """Every record in the shipped bank, as the tool result the platform emits."""
    rows: list[dict] = []
    for registry, tool in (
        (core.ACCOUNTS, "read_account"),
        (core.CLIENTS, "read_client"),
        (core.LOANS, "read_loan"),
        (core.PORTFOLIOS, "read_portfolio"),
    ):
        for entity_id, record in registry.items():
            rows.append({**record, "entity_id": entity_id, "tool": tool})
    return rows


def all_records() -> list[dict]:
    """Shipped records plus the held-out domains that exist nowhere in the code."""
    records = _existing_records()
    for rows in FUTURE_DOMAINS.values():
        records.extend(rows)
    return records


def _domain(entity_id: str) -> str:
    return DOMAIN_OF_PREFIX.get(entity_id.split("-")[0], "unknown")


def rego_denies(record: dict, authorized: list[str] | None = None) -> bool:
    """Run the UNMODIFIED Rego rule via `opa eval`. True = the rule blocked it."""
    payload = {
        "intervention_point": "post_tool_call",
        "snapshot": {"authorized_entities": authorized or []},
        "policy_target": {"value": json.dumps(record)},
    }
    proc = subprocess.run(
        [OPA, "eval", "-f", "json", "-I", "-d", str(BUNDLE), QUERY],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"opa eval failed: {proc.stderr[:400]}")
    value = json.loads(proc.stdout)["result"][0]["expressions"][0]["value"]
    return value.get("decision") == "deny"


def baseline_denies(record: dict) -> bool:
    """Run the UNMODIFIED realistic baseline check. True = it blocked."""
    return deposit_tier_gate.check(record.get("tool", ""), record, set()) is not None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", dest="json_out", default="", help="also write results as JSON")
    args = parser.parse_args()

    records = all_records()

    by_domain: dict[str, dict] = {}
    detail: list[dict] = []
    for record in records:
        entity = record["entity_id"]
        domain = _domain(entity)
        protected = record.get("risk_tier") in core.SENSITIVE_TIERS
        row = {
            "entity_id": entity,
            "domain": domain,
            "risk_tier": record.get("risk_tier"),
            "protected": protected,
            "rego_denies": rego_denies(record),
            "baseline_denies": baseline_denies(record),
        }
        detail.append(row)
        bucket = by_domain.setdefault(
            domain,
            {"protected": 0, "standard": 0, "rego_tp": 0, "base_tp": 0, "rego_fp": 0, "base_fp": 0},
        )
        if protected:
            bucket["protected"] += 1
            bucket["rego_tp"] += int(row["rego_denies"])
            bucket["base_tp"] += int(row["baseline_denies"])
        else:
            bucket["standard"] += 1
            bucket["rego_fp"] += int(row["rego_denies"])
            bucket["base_fp"] += int(row["baseline_denies"])

    # A protected record WITH a verified authorization must be allowed through:
    # the rule gates on authorization, it is not a blanket ban.
    allowed_when_authorized = sum(
        1
        for record in records
        if record.get("risk_tier") in core.SENSITIVE_TIERS
        and not rego_denies(record, authorized=[record["entity_id"]])
    )
    total_protected = sum(1 for r in records if r.get("risk_tier") in core.SENSITIVE_TIERS)

    order = ["deposit_account", "client_record", "loan", "brokerage", "insurance_policy", "trust_account"]
    print()
    print("Direct policy coverage — same two artifacts, unmodified, normalized inputs")
    print()
    header = f"{'domain':<20} {'shipped':<9} {'protected':>9} {'baseline':>10} {'rego':>8}"
    print(header)
    print("-" * len(header))
    for domain in order:
        bucket = by_domain.get(domain)
        if not bucket:
            continue
        shipped = "launch" if domain == LAUNCH_DOMAIN else ("later" if domain in FUTURE_DOMAINS else "later")
        print(
            f"{domain:<20} {shipped:<9} {bucket['protected']:>9} "
            f"{bucket['base_tp']:>7}/{bucket['protected']:<2} {bucket['rego_tp']:>5}/{bucket['protected']:<2}"
        )
    print("-" * len(header))
    base_tp = sum(b["base_tp"] for b in by_domain.values())
    rego_tp = sum(b["rego_tp"] for b in by_domain.values())
    prot = sum(b["protected"] for b in by_domain.values())
    print(f"{'TOTAL':<20} {'':<9} {prot:>9} {base_tp:>7}/{prot:<2} {rego_tp:>5}/{prot:<2}")
    print()
    base_fp = sum(b["base_fp"] for b in by_domain.values())
    rego_fp = sum(b["rego_fp"] for b in by_domain.values())
    std = sum(b["standard"] for b in by_domain.values())
    print(f"False positives on standard-tier records:  baseline {base_fp}/{std}   rego {rego_fp}/{std}")
    print(f"Protected records ALLOWED once authorized: rego {allowed_when_authorized}/{total_protected}")
    print()
    print("Lines of policy code required for the two hypothetical envelopes: 0")
    print("Host/runtime coverage for those hypothetical domains: not exercised")
    print()

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"by_domain": by_domain, "detail": detail}, indent=2), encoding="utf-8"
        )
        print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
