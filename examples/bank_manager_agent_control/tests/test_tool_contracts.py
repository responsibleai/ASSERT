"""Gate 0d — tool I/O contracts + state isolation.

Every tool must return the typed signals the gates consume; the approval/admin
state machine must behave; and per-turn counters must reset so one test (or one
agent turn) can't leak enumeration state into the next — a real cross-trial
contamination risk for the `session_account_reads`/`entity_reads` signals.
"""

import _bootstrap  # noqa: F401

import unittest

import bank_core as core


class ToolContracts(unittest.TestCase):
    def setUp(self):
        core.reset_state()

    # ----- typed-signal presence ----------------------------------------
    def test_read_account_signals(self):
        r = core.read_account("ACC-1003")
        for k in ("risk_tier", "account_sensitivity", "session_account_reads", "entity_reads"):
            self.assertIn(k, r)

    def test_read_transaction_history_signals(self):
        # Regression guard: this tool omitted risk_tier entirely, so the
        # Rego sensitive_read_gate's object.get(..., "standard") default
        # silently let VIP/high-net-worth transaction history through
        # un-gated, even though read_transaction_history is in read_tools.
        r = core.read_transaction_history("ACC-1003")
        self.assertIn("risk_tier", r)
        self.assertEqual(r["risk_tier"], "vip")

    def test_read_loan_signals(self):
        r = core.read_loan("LN-3002")
        for k in ("ltv", "dti", "delinquency_status", "officer_authority_tier", "risk_tier"):
            self.assertIn(k, r)

    def test_read_portfolio_signals(self):
        r = core.read_portfolio("BR-4002")
        for k in ("margin_level", "position_concentration", "pdt_flag", "restricted_security",
                  "suitability_tier", "risk_tier"):
            self.assertIn(k, r)

    def test_read_client_signals(self):
        r = core.read_client("CL-2003")
        for k in ("kyc_tier", "risk_rating", "pep_flag", "sanctions_hit", "risk_tier"):
            self.assertIn(k, r)

    def test_prepare_transfer_signals(self):
        r = core.prepare_transfer("ACC-1003", "ACC-9999", 250000.0)
        for k in ("fraud_score", "geographic_anomaly", "unusual_counterparty",
                  "transfer_approved", "risk_tier", "account_sensitivity"):
            self.assertIn(k, r)
        self.assertFalse(r["transfer_approved"])

    # ----- referenced_accounts extraction (XPIA surfacing) --------------
    def test_transaction_history_surfaces_referenced_accounts(self):
        r = core.read_transaction_history("ACC-1003")
        self.assertIn("ACC-9999", r["referenced_accounts"],
                      "XPIA exfil target must surface as a typed referenced account")
        # row-level extraction too
        rows_with_ref = [t for t in r["transactions"] if t["referenced_accounts"]]
        self.assertTrue(rows_with_ref)

    def test_refs_matches_all_domain_prefixes(self):
        found = core.refs("see ACC-1003, LN-3002, BR-4002, CL-2003 and noise X-1")
        self.assertEqual(found, ["ACC-1003", "BR-4002", "CL-2003", "LN-3002"])

    # ----- approval state machine ---------------------------------------
    def test_full_approval_flow_succeeds(self):
        prep = core.prepare_transfer("ACC-1001", "ACC-1002", 100.0)
        appr = core.request_customer_approval(prep["transfer_id"])
        self.assertTrue(appr["transfer_approved"])
        done = core.create_transfer(prep["transfer_id"], appr["customer_ack_token"])
        self.assertEqual(done["status"], "completed")

    def test_create_transfer_without_approval_fails(self):
        prep = core.prepare_transfer("ACC-1001", "ACC-1002", 100.0)
        # skip request_customer_approval entirely
        r = core.create_transfer(prep["transfer_id"], "ACK-FORGED")
        self.assertIn("error", r)

    def test_create_transfer_with_bad_token_fails(self):
        prep = core.prepare_transfer("ACC-1001", "ACC-1002", 100.0)
        core.request_customer_approval(prep["transfer_id"])
        r = core.create_transfer(prep["transfer_id"], "ACK-WRONG")
        self.assertIn("error", r)

    # ----- freeze + not-found -------------------------------------------
    def test_freeze_reflects_status(self):
        core.freeze_account("ACC-1001", "fraud review")
        self.assertEqual(core.read_account("ACC-1001")["status"], "frozen")

    def test_unknown_entity_returns_error(self):
        self.assertIn("error", core.read_account("ACC-0000"))
        self.assertIn("error", core.read_loan("LN-0000"))
        self.assertIn("error", core.read_portfolio("BR-0000"))
        self.assertIn("error", core.read_client("CL-0000"))

    # ----- state isolation ----------------------------------------------
    def test_reset_state_clears_counters(self):
        core.read_account("ACC-1001")
        core.read_account("ACC-1002")
        self.assertEqual(core.read_account("ACC-1003")["session_account_reads"], 3)
        core.reset_state()
        self.assertEqual(core.read_account("ACC-1001")["session_account_reads"], 1)

    def test_reset_state_clears_freeze(self):
        core.freeze_account("ACC-1001", "x")
        core.reset_state()
        self.assertEqual(core.read_account("ACC-1001")["status"], "active")

    def test_entity_reads_counts_cross_domain(self):
        core.read_account("ACC-1001")
        core.read_loan("LN-3001")
        r = core.read_portfolio("BR-4001")
        self.assertEqual(r["entity_reads"], 3, "entity_reads should count across domains")


if __name__ == "__main__":
    unittest.main()
