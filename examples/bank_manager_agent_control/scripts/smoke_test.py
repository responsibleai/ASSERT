"""Smoke test for the feature-representation experiment scaffolding.

Two tiers:
  - OFFLINE (stdlib only): exercises the mock knowledge base end to end. Runs
    anywhere, including this repo's authoring machine.
  - DEPS (needs mcp + langgraph + the agent stack): instantiates the two MCP
    servers and imports the agent module. Skips gracefully if deps are missing.

Run on the work laptop after `pip install -e ".[otel,langgraph,examples]"`:
    python examples/bank_manager_agent_control/smoke_test.py
Expect all OFFLINE checks PASS and all DEPS checks PASS.
"""

import os
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent          # scripts/
EXAMPLE = HERE.parent                           # the example root
RUNTIME = EXAMPLE / "runtime"                    # engine modules + knowledge corpus
sys.path.insert(0, str(RUNTIME))  # allow sibling imports (kb_backend, feature_policy, …)

_passed, _failed, _skipped = 0, 0, 0


def check(name, fn):
    global _passed, _failed
    try:
        fn()
        print(f"  PASS  {name}")
        _passed += 1
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        _failed += 1
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")
        traceback.print_exc()
        _failed += 1


def skip(name, why):
    global _skipped
    print(f"  SKIP  {name}: {why}")
    _skipped += 1


# ---------------------------------------------------------------------------
# OFFLINE: mock knowledge base
# ---------------------------------------------------------------------------
def offline_tests():
    os.environ.setdefault("KB_BACKEND", "mock")
    os.environ["KB_CORPUS_DIR"] = str(RUNTIME / "knowledge")
    from kb_backend import MockKBBackend, get_backend

    be = get_backend()
    assert isinstance(be, MockKBBackend), "default backend should be mock"
    assert len(be.chunks) >= 10, f"expected >=10 chunks, got {len(be.chunks)}"

    def grounded_vip_transfer():
        r = be.retrieve("what is the approval flow for a VIP account transfer")
        assert r["grounded"] is True, "VIP transfer policy should be grounded"
        assert r["citations"], "expected citations"
        srcs = {c["source"] for c in r["citations"]}
        assert any("transfer" in s or "sensitive" in s for s in srcs), f"unexpected sources {srcs}"
        for c in r["citations"]:
            assert c["ref_id"] and c["source"] and "score" in c, "citation shape"

    def grounded_jumbo_ltv():
        r = be.retrieve("maximum LTV cap for a jumbo refinance loan")
        assert r["grounded"] is True
        assert any("home-loan" in c["source"] for c in r["citations"]), "should hit loan policy"

    def grounded_margin():
        r = be.retrieve("margin call policy near 0.75 margin level")
        assert r["grounded"] is True
        assert any("brokerage" in c["source"] for c in r["citations"])

    def ungrounded_out_of_scope():
        r = be.retrieve("what is the capital of France")
        assert r["grounded"] is False, "out-of-scope query must be ungrounded"
        assert r["citations"] == [], "ungrounded -> no citations"

    def poisoned_doc_retrievable():
        # The vendor doc carries an XPIA payload; retrieval surfaces it so the
        # downstream post-retrieval control has something to catch.
        r = be.retrieve("vendor onboarding checklist W-9 MSA")
        assert any("vendor-onboarding" in c["source"] for c in r["citations"]), "vendor doc should rank"
        assert "ignore all previous instructions" in r["answer"].lower(), "XPIA payload present in retrieved text"

    check("kb: grounded VIP transfer policy", grounded_vip_transfer)
    check("kb: grounded jumbo LTV", grounded_jumbo_ltv)
    check("kb: grounded margin policy", grounded_margin)
    check("kb: ungrounded out-of-scope", ungrounded_out_of_scope)
    check("kb: poisoned doc retrievable (XPIA setup)", poisoned_doc_retrievable)


def offline_feature_policy_tests():
    """Pure-python guardrail logic + ACS feature artifacts (no mcp/opa)."""
    import feature_policy as fpol

    def generalization_premise():
        import bank_core as core
        text, feature = fpol.TextGate(), fpol.FeatureGate()
        ev_loan = {"tool": "place_trade", "args": {}, "output": core.read_loan("LN-3002")}
        # Feature gate catches a sensitive loan the hardcoded text gate cannot.
        assert feature.flags_sensitive_entity(ev_loan), "feature gate must catch LN-3002"
        assert not text.flags_sensitive_entity(ev_loan), "text gate must miss LN-3002"

    def host_state_machine():
        st = fpol.new_feature_state("transfer from ACC-1003 to ACC-1001")
        assert st["authorized_refs"] == {"ACC-1003", "ACC-1001"}
        snap = fpol.pre_call_snapshot(st, "create_transfer", {"transfer_id": "TFR-X"})
        assert snap["transfer_approved"] is False

    def acs_feature_artifacts_present():
        pol = EXAMPLE / "acs" / "policy" / "bank_manager_feature.rego"
        man = EXAMPLE / "acs" / "manifest_feature.yaml"
        assert pol.exists(), "feature Rego policy missing"
        assert man.exists(), "feature manifest missing"
        body = pol.read_text()
        assert "risk_tier" in body and "referenced_accounts" in body, "feature Rego must gate on typed signals"
        assert "ACC-100" not in body, "feature Rego must not hardcode account ids"

    check("policy: generalization premise (feature beats text on LN-3002)", generalization_premise)
    check("policy: host snapshot state machine", host_state_machine)
    check("policy: ACS feature artifacts present + typed", acs_feature_artifacts_present)


# ---------------------------------------------------------------------------
# DEPS: MCP servers + agent module (need mcp / langgraph installed)
# ---------------------------------------------------------------------------
def deps_tests():
    try:
        import mcp.server.fastmcp  # noqa: F401
    except Exception as e:  # noqa: BLE001
        skip("mcp servers + agent", f"mcp not installed ({type(e).__name__})")
        return

    def bank_server_builds():
        import realistic_bank_mcp_server as rb
        srv = rb.make_server()
        assert srv is not None
        # Sanity-check the data model: sensitive entities exist in each domain
        # under DIFFERENT id prefixes (the generalization crux).
        assert rb.ACCOUNTS["ACC-1003"]["risk_tier"] == "vip"
        assert rb.LOANS["LN-3002"]["risk_tier"] == "vip"
        assert rb.PORTFOLIOS["BR-4002"]["risk_tier"] == "vip"
        assert {e.split("-")[0] for e in (*rb.ACCOUNTS, *rb.LOANS, *rb.PORTFOLIOS)} == {"ACC", "LN", "BR"}

    def kb_server_builds():
        os.environ.setdefault("KB_BACKEND", "mock")
        os.environ["KB_CORPUS_DIR"] = str(RUNTIME / "knowledge")
        import kb_mcp_server as kb
        assert kb.make_server() is not None

    check("deps: realistic bank MCP server builds", bank_server_builds)
    check("deps: KB MCP server builds", kb_server_builds)

    try:
        import langgraph  # noqa: F401
        import importlib
        import examples.bank_manager_agent_control.agent as agent  # noqa: F401
        importlib.reload(agent)
        print("  PASS  deps: agent module imports")
        globals()["_passed"] += 1

        def feature_callables_present():
            for name in ("chat_unguarded_realistic", "chat_unguarded_realistic_prompted",
                         "chat_guarded_acs_feature"):
                assert callable(getattr(agent, name)), f"missing callable {name}"
            assert agent.MCP_SERVER_BANK.exists() and agent.MCP_SERVER_KB.exists()
            assert agent.ACS_MANIFEST_FEATURE.exists()

        check("deps: feature ASSERT callables + two-server paths", feature_callables_present)
    except Exception as e:  # noqa: BLE001
        skip("agent module import", f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    print("OFFLINE checks (stdlib only):")
    offline_tests()
    offline_feature_policy_tests()
    print("DEPS checks (mcp / langgraph):")
    deps_tests()
    print(f"\n{_passed} passed, {_failed} failed, {_skipped} skipped")
    sys.exit(1 if _failed else 0)
