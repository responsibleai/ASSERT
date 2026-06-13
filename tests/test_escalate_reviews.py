# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Unit tests for the deterministic review-escalation routing.

Covers the governance contract from AGENTS.md / dev-maintainer.md and the three
semantics fixes from the #232 review: the 7d fallback must be reachable for a
requested-but-silent PR, the fallback step must never request the PR author, and
the routing must match the documented exclusion order.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The escalation script lives under .github/scripts (not an importable package),
# so load it by file path.
_SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "escalate_reviews.py"
_spec = importlib.util.spec_from_file_location("escalate_reviews", _SCRIPT)
esc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = esc  # needed so @dataclass can resolve the module
_spec.loader.exec_module(esc)


def _iso(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rules(mapping: list[tuple[str, list[str]]]) -> list:
    return [esc.CodeownersRule(pattern=p, owners=o) for p, o in mapping]


def _pr(number, author, paths, age_hours, requested=None, reviewed=None):
    return {
        "number": number,
        "title": f"PR {number}",
        "author": {"login": author},
        "createdAt": _iso(age_hours),
        "files": [{"path": p} for p in paths],
        "reviewRequests": [{"login": r} for r in (requested or [])],
        "reviews": [{"author": {"login": a}, "state": "COMMENTED"} for a in (reviewed or [])],
    }


class EscalationRoutingTest(unittest.TestCase):
    def setUp(self):
        # Deterministic: no one is OOO.
        self._orig_ooo = esc._is_ooo
        esc._is_ooo = lambda login: False
        # Use a non-Chang fallback so the author-guard cases are explicit.
        self._orig_fb = esc.FALLBACK_LOGIN
        esc.FALLBACK_LOGIN = "admin"
        self.rules = _rules([
            ("*", ["admin"]),                       # catch-all / fallback
            ("src/", ["alice", "bob", "admin"]),    # specific owners
        ])

    def tearDown(self):
        esc._is_ooo = self._orig_ooo
        esc.FALLBACK_LOGIN = self._orig_fb

    def ev(self, pr, min_age=24.0):
        return esc.evaluate_pr("o/r", pr, self.rules, min_age)

    # ── bug 2: 7d fallback must be reachable for a requested-but-silent PR ──
    def test_7d_fallback_reachable_when_requested_and_silent(self):
        pr = _pr(1, "alice", ["src/x.py"], age_hours=200, requested=["bob"])
        d = self.ev(pr)
        self.assertEqual(d.action, "fallback", d.reason)
        self.assertEqual(d.chosen, ["admin"])

    # ── bug 3: fallback must never request the PR author ──
    def test_fallback_never_requests_author(self):
        # Admin (the fallback) authored a PR whose only owner is the catch-all.
        pr = _pr(2, "admin", ["README.md"], age_hours=300, requested=[])
        d = self.ev(pr)
        self.assertEqual(d.action, "manual", d.reason)
        self.assertEqual(d.chosen, [])
        self.assertNotIn("admin", d.chosen)

    # ── 72h adds a second non-fallback owner, not the reserved fallback ──
    def test_72h_adds_second_nonfallback_owner(self):
        pr = _pr(3, "alice", ["src/x.py"], age_hours=80, requested=["bob"])
        d = self.ev(pr)
        # bob requested; alice is author; remaining non-fallback owner = none
        # (only admin left) → reserve admin for 7d → observe.
        self.assertEqual(d.action, "observe", d.reason)

    def test_72h_picks_real_second_owner_when_available(self):
        rules = _rules([("*", ["admin"]), ("src/", ["alice", "bob", "carol", "admin"])])
        pr = _pr(4, "alice", ["src/x.py"], age_hours=80, requested=["bob"])
        d = esc.evaluate_pr("o/r", pr, rules, 24.0)
        self.assertEqual(d.action, "request-second", d.reason)
        self.assertEqual(d.chosen, ["carol"])  # non-fallback, not the author/requested

    # ── 24h first request assigns an owner, excludes author ──
    def test_first_request_excludes_author(self):
        pr = _pr(5, "alice", ["src/x.py"], age_hours=30, requested=[])
        d = self.ev(pr)
        self.assertEqual(d.action, "request")
        self.assertNotIn("alice", d.chosen)
        self.assertEqual(d.chosen, ["bob"])  # coverage tie → alphabetical, author excluded

    # ── already-reviewed → observe ──
    def test_observe_when_reviewer_responded(self):
        pr = _pr(6, "alice", ["src/x.py"], age_hours=300, requested=["bob"], reviewed=["bob"])
        d = self.ev(pr)
        self.assertEqual(d.action, "observe")

    # ── below the first window → observe ──
    def test_below_window_observes(self):
        pr = _pr(7, "alice", ["src/x.py"], age_hours=5, requested=[])
        d = self.ev(pr)
        self.assertEqual(d.action, "observe")


if __name__ == "__main__":
    unittest.main()
