"""Gate 0g — retrieval tuning (the KB parameter the eval signal moves).

The `grounded` verdict gates on the top citation score vs a tunable floor. These
tests pin the behavior the talk's Beat-3 KB-tuning move depends on:
  - default floor reproduces prior grounding,
  - raising the floor refuses an in-corpus query (recall miss), lowering restores,
  - the grounded flag is exactly `top_score >= floor`,
  - recall is monotone non-increasing as the floor rises,
  - the Foundry reranker-threshold filter drops sub-threshold citations.
"""

import _bootstrap  # noqa: F401

import unittest

from kb_backend import MockKBBackend, apply_score_threshold

CORPUS = str(__import__("pathlib").Path(_bootstrap.RUNTIME_DIR) / "knowledge")

# A borderline in-corpus query: the corpus answers it, but with a low top score.
VIP = "what is the approval flow for a VIP account transfer"
STRONG = "margin call policy near 0.75 margin level"   # high top score
OUT = "what is the capital of France"                    # no content overlap


class RetrievalTuning(unittest.TestCase):
    def _be(self, floor):
        return MockKBBackend(CORPUS, grounded_floor=floor)

    def test_default_floor_grounds_vip(self):
        self.assertTrue(self._be(None).retrieve(VIP)["grounded"])

    def test_high_floor_refuses_in_corpus(self):
        # Floor above VIP's top score (~6.56) -> the corpus answer is refused.
        r = self._be(8.0).retrieve(VIP)
        self.assertFalse(r["grounded"])
        self.assertEqual(r["citations"], [])

    def test_lowering_floor_restores(self):
        self.assertFalse(self._be(8.0).retrieve(VIP)["grounded"])
        self.assertTrue(self._be(3.0).retrieve(VIP)["grounded"])

    def test_strong_query_survives_high_floor(self):
        # A high-confidence in-corpus query (~21.9) stays grounded at floor 8.
        self.assertTrue(self._be(8.0).retrieve(STRONG)["grounded"])

    def test_out_of_corpus_stays_ungrounded_any_floor(self):
        for f in (0.0, 3.0, 8.0):
            self.assertFalse(self._be(f).retrieve(OUT)["grounded"])

    def test_grounded_equals_top_ge_floor(self):
        raw = self._be(0.0).retrieve(VIP)
        top = max(c["score"] for c in raw["citations"])
        for f in (top - 1.0, top + 1.0):
            self.assertEqual(self._be(f).retrieve(VIP)["grounded"], top >= f)

    def test_recall_monotone_in_floor(self):
        queries = [VIP, STRONG,
                   "maximum LTV cap for a jumbo refinance loan",
                   "what documents are required for KYC tier 2 onboarding"]
        prev = None
        for f in (0.0, 3.0, 5.0, 8.0, 15.0, 25.0):
            grounded = sum(self._be(f).retrieve(q)["grounded"] for q in queries)
            if prev is not None:
                self.assertLessEqual(grounded, prev)  # non-increasing as floor rises
            prev = grounded

    def test_reranker_threshold_filter(self):
        cites = [{"ref_id": "a", "score": 3.9}, {"ref_id": "b", "score": 1.2},
                 {"ref_id": "c", "score": 2.5}]
        self.assertEqual(len(apply_score_threshold(cites, None)), 3)      # off
        kept = apply_score_threshold(cites, 2.5)
        self.assertEqual({c["ref_id"] for c in kept}, {"a", "c"})
        self.assertEqual(apply_score_threshold(cites, 9.0), [])           # all dropped


if __name__ == "__main__":
    unittest.main()
