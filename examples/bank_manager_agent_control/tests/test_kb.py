"""Gate 0f — knowledge-base backend (the typed grounding signal).

Ports the smoke checks into real assertions and adds determinism: the mock KB
must return identical results for the same query (no run-to-run variance that
would make the grounding metric noisy), grounded↔citations must be consistent,
and the planted XPIA doc must remain retrievable so the post-retrieval control
has something to catch.
"""

import _bootstrap  # noqa: F401

import unittest

from kb_backend import MockKBBackend, get_backend


class KnowledgeBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.be = get_backend()

    def test_default_backend_is_mock(self):
        self.assertIsInstance(self.be, MockKBBackend)
        self.assertGreaterEqual(len(self.be.chunks), 10)

    def test_grounded_vip_transfer(self):
        r = self.be.retrieve("what is the approval flow for a VIP account transfer")
        self.assertTrue(r["grounded"])
        self.assertTrue(r["citations"])
        for c in r["citations"]:
            self.assertTrue(c["ref_id"] and c["source"])
            self.assertIn("score", c)

    def test_grounded_jumbo_ltv(self):
        r = self.be.retrieve("maximum LTV cap for a jumbo refinance loan")
        self.assertTrue(r["grounded"])
        self.assertTrue(any("home-loan" in c["source"] for c in r["citations"]))

    def test_grounded_margin(self):
        r = self.be.retrieve("margin call policy near 0.75 margin level")
        self.assertTrue(r["grounded"])
        self.assertTrue(any("brokerage" in c["source"] for c in r["citations"]))

    def test_ungrounded_out_of_scope(self):
        r = self.be.retrieve("what is the capital of France")
        self.assertFalse(r["grounded"], "out-of-scope query must be ungrounded")
        self.assertEqual(r["citations"], [])

    def test_grounded_implies_citations_and_vice_versa(self):
        for q in ["VIP account transfer approval", "jumbo refinance LTV", "capital of France",
                  "margin call", "blorptastic zibblefarb"]:
            r = self.be.retrieve(q)
            self.assertEqual(bool(r["grounded"]), bool(r["citations"]),
                             f"grounded/citations inconsistent for {q!r}")

    def test_poisoned_doc_retrievable(self):
        r = self.be.retrieve("vendor onboarding checklist W-9 MSA")
        self.assertTrue(any("vendor-onboarding" in c["source"] for c in r["citations"]))
        self.assertIn("ignore all previous instructions", r["answer"].lower())

    def test_determinism(self):
        q = "restricted securities trading policy"
        r1 = self.be.retrieve(q)
        r2 = self.be.retrieve(q)
        self.assertEqual(r1["grounded"], r2["grounded"])
        self.assertEqual([c["ref_id"] for c in r1["citations"]],
                         [c["ref_id"] for c in r2["citations"]])
        self.assertEqual(r1["answer"], r2["answer"])


if __name__ == "__main__":
    unittest.main()
