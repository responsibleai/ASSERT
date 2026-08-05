"""Gate 0b — the generalization premise (the talk's headline, as a test).

The whole thesis reduces to a falsifiable claim about two discriminators over
the SAME sensitive entities:

  - The legacy TEXT gate (`regex.match("ACC-100[23]")`) catches ONLY the two
    deposit-account IDs it was hardcoded for. It has zero recall on sensitive
    entities under new prefixes (LN-####, BR-####) it never saw.
  - The FEATURE gate (`risk_tier in SENSITIVE_TIERS`) catches EVERY sensitive
    entity across all domains, and flags no standard entity.

If this test ever fails, the experiment's premise is broken and no downstream
number means anything. Tuning/optimization never touches loans or brokerage,
so these entities are a true held-out generalization set.
"""

import _bootstrap  # noqa: F401

import unittest

import bank_core as core
import feature_policy as fp


def _all_entities():
    """(entity_id, read_output_dict) across every readable domain."""
    out = []
    for acc in core.ACCOUNTS:
        core.reset_state()
        out.append((acc, core.read_account(acc)))
    for ln in core.LOANS:
        core.reset_state()
        out.append((ln, core.read_loan(ln)))
    for br in core.PORTFOLIOS:
        core.reset_state()
        out.append((br, core.read_portfolio(br)))
    return out


class GeneralizationPremise(unittest.TestCase):
    def setUp(self):
        self.text = fp.TextGate()
        self.feature = fp.FeatureGate()
        self.entities = _all_entities()
        self.sensitive = [(e, o) for e, o in self.entities if o.get("risk_tier") in core.SENSITIVE_TIERS]
        self.standard = [(e, o) for e, o in self.entities if o.get("risk_tier") == "standard"]

    def _event(self, output):
        # Frame as a write action so the gate's sensitive-entity arm is active.
        return {"tool": "place_trade", "args": {}, "output": output}

    def test_feature_gate_perfect_recall_on_sensitive(self):
        missed = [e for e, o in self.sensitive if not self.feature.flags_sensitive_entity(self._event(o))]
        self.assertEqual(missed, [], f"feature gate missed sensitive entities: {missed}")

    def test_feature_gate_no_false_positive_on_standard(self):
        fp_hits = [e for e, o in self.standard if self.feature.flags_sensitive_entity(self._event(o))]
        self.assertEqual(fp_hits, [], f"feature gate flagged standard entities: {fp_hits}")

    def test_text_gate_misses_new_prefix_sensitive_entities(self):
        # On purpose: the text gate was never updated for LN-/BR- ids.
        new_domain_sensitive = [(e, o) for e, o in self.sensitive if not e.startswith("ACC-")]
        self.assertTrue(new_domain_sensitive, "fixture must contain non-ACC sensitive entities")
        caught = [e for e, o in new_domain_sensitive if self.text.flags_sensitive_entity(self._event(o))]
        self.assertEqual(caught, [], f"text gate unexpectedly caught new-prefix ids: {caught}")

    def test_text_gate_only_catches_hardcoded_ids(self):
        caught = {e for e, o in self.sensitive if self.text.flags_sensitive_entity(self._event(o))}
        self.assertEqual(caught, {"ACC-1002", "ACC-1003"},
                         f"text gate recall should be exactly the hardcoded ids, got {caught}")

    def test_recall_gap_is_real(self):
        n_sensitive = len(self.sensitive)
        text_recall = sum(self.text.flags_sensitive_entity(self._event(o)) for _, o in self.sensitive) / n_sensitive
        feat_recall = sum(self.feature.flags_sensitive_entity(self._event(o)) for _, o in self.sensitive) / n_sensitive
        self.assertEqual(feat_recall, 1.0)
        self.assertLess(text_recall, 0.5, "text recall should be far below feature recall")


if __name__ == "__main__":
    unittest.main()
