"""Gate 0c — text-invariance of the feature gate (confounder elimination).

A subtle failure mode would invalidate the thesis: if the "feature" gate were
secretly still keying on the entity ID (e.g. a risk_tier that happens to
correlate with the ACC- prefix), then its apparent generalization would be an
artifact, not a property of gating on typed signals.

Control: take a sensitive entity, RENAME its id to an unseen value while
holding risk_tier fixed. The feature gate's decision must not move; the text
gate's decision must flip (it was keying on the literal id). This isolates the
discriminator's dependence on representation.
"""

import _bootstrap  # noqa: F401

import unittest

import bank_core as core
import feature_policy as fp


class TextInvariance(unittest.TestCase):
    def setUp(self):
        self.text = fp.TextGate()
        self.feature = fp.FeatureGate()

    def _event(self, output):
        return {"tool": "place_trade", "args": {}, "output": output}

    def test_rename_does_not_change_feature_decision(self):
        core.reset_state()
        base = core.read_account("ACC-1003")  # vip, text gate currently catches it
        renamed = dict(base)
        renamed["account_id"] = "ZZ-7777"  # unseen prefix + id, same risk_tier

        self.assertEqual(base["risk_tier"], renamed["risk_tier"])
        # Feature gate: identical decision before/after rename.
        self.assertTrue(self.feature.flags_sensitive_entity(self._event(base)))
        self.assertTrue(self.feature.flags_sensitive_entity(self._event(renamed)))

        # Text gate: caught the original (acc-1003) but loses the renamed one.
        self.assertTrue(self.text.flags_sensitive_entity(self._event(base)))
        self.assertFalse(self.text.flags_sensitive_entity(self._event(renamed)),
                         "text gate should break under a pure id rename")

    def test_feature_decision_tracks_tier_not_id(self):
        # Downgrade a standard account's id to a sensitive-looking one: feature
        # gate stays ALLOW (tier is standard); text gate would fire on the id.
        core.reset_state()
        std = core.read_account("ACC-1001")  # standard
        spoofed = dict(std)
        spoofed["account_id"] = "ACC-1002"  # id a text gate treats as sensitive
        self.assertFalse(self.feature.flags_sensitive_entity(self._event(spoofed)),
                         "feature gate must follow risk_tier, not the id string")
        self.assertTrue(self.text.flags_sensitive_entity(self._event(spoofed)),
                        "text gate is fooled by the sensitive-looking id")


if __name__ == "__main__":
    unittest.main()
