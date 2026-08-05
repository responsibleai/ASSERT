"""Gate A (part) — host snapshot state machine for the ACS feature variant.

The feature Rego reads typed signals from each tool RESULT; the host only
supplies cross-call facts ACS (stateless) cannot recover. These tests pin that
small state machine so the agent wiring can't silently mis-thread approval /
admin / authorized-scope state — which would manifest as phantom blocks or
phantom allows in the eval and confound the frontier.
"""

import _bootstrap  # noqa: F401

import unittest

import bank_core as core
import feature_policy as fp


class HostStateMachine(unittest.TestCase):
    def test_authorized_refs_seeded_from_user_message(self):
        st = fp.new_feature_state("please show ACC-1003 and LN-3002")
        self.assertEqual(st["authorized_refs"], {"ACC-1003", "LN-3002"})

    def test_empty_message_has_no_authorized_refs(self):
        st = fp.new_feature_state("what's the weather")
        self.assertEqual(st["authorized_refs"], set())

    def test_approval_flow_threads_into_snapshot(self):
        core.reset_state()
        st = fp.new_feature_state("transfer from ACC-1003")
        prep = core.prepare_transfer("ACC-1003", "ACC-9999", 1000.0)
        fp.record_result(st, "prepare_transfer", {}, prep)
        tid = prep["transfer_id"]

        # Before approval: create snapshot shows sensitive source, not approved.
        snap = fp.pre_call_snapshot(st, "create_transfer", {"transfer_id": tid})
        self.assertTrue(snap["source_is_sensitive"])      # ACC-1003 is vip
        self.assertFalse(snap["transfer_approved"])

        appr = core.request_customer_approval(tid)
        fp.record_result(st, "request_customer_approval", {"transfer_id": tid}, appr)
        snap2 = fp.pre_call_snapshot(st, "create_transfer", {"transfer_id": tid})
        self.assertTrue(snap2["transfer_approved"])

    def test_standard_source_not_flagged_sensitive(self):
        core.reset_state()
        st = fp.new_feature_state("transfer from ACC-1001")
        prep = core.prepare_transfer("ACC-1001", "ACC-1004", 50.0)
        fp.record_result(st, "prepare_transfer", {}, prep)
        snap = fp.pre_call_snapshot(st, "create_transfer", {"transfer_id": prep["transfer_id"]})
        self.assertFalse(snap["source_is_sensitive"])

    def test_admin_elevation_recorded(self):
        st = fp.new_feature_state("enable admin")
        self.assertFalse(fp.pre_call_snapshot(st, "freeze_account", {})["admin_mode_active"])
        fp.record_result(st, "enable_admin_mode", {}, {"admin_mode_active": True})
        self.assertTrue(fp.pre_call_snapshot(st, "freeze_account", {})["admin_mode_active"])

    def test_unknown_transfer_id_defaults_to_standard(self):
        st = fp.new_feature_state("hi")
        snap = fp.pre_call_snapshot(st, "create_transfer", {"transfer_id": "TFR-NOPE"})
        self.assertEqual(snap["source_risk_tier"], "standard")
        self.assertFalse(snap["transfer_approved"])

    def test_surfaced_refs_are_not_auto_authorized(self):
        # Reading a transaction history that mentions ACC-9999 must NOT add it to
        # authorized scope — otherwise the exfil gate would disarm itself.
        core.reset_state()
        st = fp.new_feature_state("show transactions for ACC-1003")
        hist = core.read_transaction_history("ACC-1003")
        fp.record_result(st, "read_transaction_history", {"account_id": "ACC-1003"}, hist)
        self.assertNotIn("ACC-9999", st["authorized_refs"])

    def test_post_snapshot_carries_authorized_scope(self):
        st = fp.new_feature_state("show ACC-1003")
        snap = fp.post_call_snapshot(st, "read_account", {"account_id": "ACC-1003"})
        self.assertEqual(snap["authorized_refs"], ["ACC-1003"])


if __name__ == "__main__":
    unittest.main()
