"""Calibration harness — the honest version of the "metric that can be faked" story.

Runs BOTH gates over the same labelled set (``coercion_labels.jsonl``), on the
same split, through the same recall test, and reports:

  recall  — the metric a recall-only acceptance test would gate on. Both gates
            tie here, which is the whole point: recall alone cannot tell a
            working gate from a broken one.
  FPR     — false-positive rate on the legitimate half (verified escalations,
            routine flow, benign manager phrasing). This is where the naive
            keyword gate collapses.
  Brier   — mean squared error of the emitted probability against the label.
            Rewards a gate that is *uncertain when it should be*; punishes a
            gate that is confidently wrong.

Protocol
--------
1. Score every case with both gates (the LLM gate is scored ONCE; the raw score
   is reused for the raw/calibrated comparison so the comparison is apples to
   apples).
2. Split stratified: calibration = every 1-in-{k}, held-out = the rest.
3. Fit Platt scaling (a, b) on the CALIBRATION split only.
4. Select escalate_lo / deny_hi on the CALIBRATION split only, against an
   explicit objective: maximise recall subject to FPR <= --max-fpr.
5. Report all three gates on the HELD-OUT split.

Every number printed is computed here from actual model output. Nothing is
hardcoded.

Usage
-----
    python scripts/coercion_calibration.py --model gpt-4o-mini
    python scripts/coercion_calibration.py --offline   # naive gate only, no API
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

EXAMPLE_DIR = Path(__file__).resolve().parents[1]
RUNTIME_DIR = EXAMPLE_DIR / "runtime"
sys.path.insert(0, str(RUNTIME_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv(EXAMPLE_DIR.parents[1] / ".env")
except ImportError:  # pragma: no cover
    pass

import coercion_classifier as cc  # noqa: E402

LABELS_PATH = RUNTIME_DIR / "coercion_labels.jsonl"
FIT_PATH = RUNTIME_DIR / "coercion_calibration.json"
REPORT_PATH = EXAMPLE_DIR / "artifacts" / "coercion_calibration_report.json"


# ── metrics ────────────────────────────────────────────────────────────────

def confusion(scores: list[float], labels: list[int], threshold: float) -> dict:
    tp = sum(1 for s, y in zip(scores, labels) if s > threshold and y == 1)
    fn = sum(1 for s, y in zip(scores, labels) if s <= threshold and y == 1)
    fp = sum(1 for s, y in zip(scores, labels) if s > threshold and y == 0)
    tn = sum(1 for s, y in zip(scores, labels) if s <= threshold and y == 0)
    return {"tp": tp, "fn": fn, "fp": fp, "tn": tn,
            "recall": tp / (tp + fn) if (tp + fn) else 0.0,
            "fpr": fp / (fp + tn) if (fp + tn) else 0.0,
            "precision": tp / (tp + fp) if (tp + fp) else 0.0}


def brier(scores: list[float], labels: list[int]) -> float:
    if not scores:
        return float("nan")
    return sum((s - y) ** 2 for s, y in zip(scores, labels)) / len(scores)


def select_thresholds(scores: list[float], labels: list[int], *, max_fpr: float,
                      deny_recall_floor: float = 0.80) -> tuple[float, float]:
    """Pick (escalate_lo, deny_hi) on the calibration split.

    Both are chosen as the MIDPOINT of the feasible plateau, not its lower edge.
    Taking the lower edge is the classic mistake: when the calibration split is
    separable there are no false positives to trade against, the search runs all
    the way down to ~0, and the deployed gate escalates on any non-zero score.
    The plateau midpoint is the max-margin choice and degrades gracefully when
    live scores drift off the calibration distribution.

    escalate_lo: over thresholds meeting the FPR budget, the recall-maximising
                 plateau, midpoint.
    deny_hi:     over thresholds with zero false positives AND recall still at
                 or above `deny_recall_floor`, the midpoint. Hard-blocking is
                 reserved for the region where we have no evidence at all of
                 harming a legitimate escalation.
    """
    grid = [i / 200.0 for i in range(200)]

    pos = [s for s, y in zip(scores, labels) if y == 1]
    neg = [s for s, y in zip(scores, labels) if y == 0]

    # Separable calibration split: there are no false positives anywhere in the
    # margin, so every threshold inside it is "optimal" and a plateau-midpoint
    # search collapses both edges onto the same point. Place them at fixed
    # quantiles of the margin instead — 25% in for escalate, 75% in for deny —
    # which is the max-margin banding and keeps a real ambiguous tier for live
    # traffic that drifts off the calibration distribution.
    if pos and neg and min(pos) > max(neg):
        lo_edge, hi_edge = max(neg), min(pos)
        width = hi_edge - lo_edge
        return round(lo_edge + 0.25 * width, 4), round(lo_edge + 0.75 * width, 4)

    feasible = [(t, confusion(scores, labels, t)) for t in grid]
    ok = [(t, c) for t, c in feasible if c["fpr"] <= max_fpr]
    if ok:
        best_recall = max(c["recall"] for _t, c in ok)
        plateau = [t for t, c in ok if c["recall"] == best_recall]
        escalate_lo = plateau[len(plateau) // 2]
    else:
        escalate_lo = cc.DEFAULT_ESCALATE_LO

    deny_ok = [t for t, c in feasible if c["fp"] == 0 and c["recall"] >= deny_recall_floor]
    deny_hi = deny_ok[len(deny_ok) // 2] if deny_ok else None
    if deny_hi is None or deny_hi <= escalate_lo:
        deny_hi = min(0.99, max(escalate_lo + 0.15, cc.DEFAULT_DENY_HI))
    return round(escalate_lo, 4), round(deny_hi, 4)


# ── scoring ────────────────────────────────────────────────────────────────

def load_cases() -> list[dict]:
    return [json.loads(line) for line in LABELS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def score_all(cases: list[dict], *, model: str | None, workers: int, offline: bool) -> list[dict]:
    if offline:
        for c in cases:
            c["raw_llm"] = None
        return cases

    def one(case: dict) -> dict:
        case["raw_llm"] = cc.raw_llm_score(case["text"], case["tool"], case.get("args"), model=model)
        return case

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, cases))


def stratified_split(cases: list[dict], every: int) -> tuple[list[dict], list[dict]]:
    """Calibration = every `every`-th case WITHIN each label class."""
    cal, held = [], []
    seen = {0: 0, 1: 0}
    for c in cases:
        y = c["label"]
        (cal if seen[y] % every == 0 else held).append(c)
        seen[y] += 1
    return cal, held


# ── main ───────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None, help="Azure deployment for the LLM gate")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--split-every", type=int, default=3, help="1-in-N cases go to the calibration split")
    ap.add_argument("--max-fpr", type=float, default=0.10, help="FPR budget for threshold selection")
    ap.add_argument("--offline", action="store_true", help="naive gate only; no API calls")
    args = ap.parse_args()

    cases = load_cases()
    print(f"[calibration] {len(cases)} labelled cases "
          f"({sum(c['label'] for c in cases)} coercive / "
          f"{sum(1 - c['label'] for c in cases)} legitimate)")

    if not args.offline:
        ok, coercive, legit = cc.discrimination_ok(model=args.model)
        print(f"[preflight] discrimination coercive={coercive:.2f} legitimate={legit:.2f} ok={ok}")
        if not ok:
            print("[preflight] FAILED — the live classifier is not separating the two "
                  "canonical cases (throttled / collapsed). Refusing to report numbers.",
                  file=sys.stderr)
            return 2

    cases = score_all(cases, model=args.model, workers=args.workers, offline=args.offline)
    for c in cases:
        c["naive"] = cc.naive_keyword_score(c["text"], c["tool"], c.get("args"))

    cal, held = stratified_split(cases, args.split_every)
    print(f"[split] calibration n={len(cal)}  held-out n={len(held)}")

    report: dict = {"n_total": len(cases), "n_cal": len(cal), "n_held": len(held),
                    "model": args.model or "(default)", "gates": {}, "cases": []}

    # ---- naive gate (no fitting; it has no parameters to fit) ----
    naive_thr = 0.5
    for name, subset in (("calibration", cal), ("held_out", held), ("full", cases)):
        s = [c["naive"] for c in subset]
        y = [c["label"] for c in subset]
        report["gates"].setdefault("naive_keyword", {})[name] = {
            **confusion(s, y, naive_thr), "brier": brier(s, y), "threshold": naive_thr}

    if args.offline:
        print(json.dumps(report["gates"], indent=2))
        return 0

    # ---- raw LLM gate, uncalibrated probability, default bands ----
    for name, subset in (("calibration", cal), ("held_out", held), ("full", cases)):
        s = [c["raw_llm"] for c in subset]
        y = [c["label"] for c in subset]
        report["gates"].setdefault("llm_raw_uncalibrated", {})[name] = {
            **confusion(s, y, cc.DEFAULT_ESCALATE_LO), "brier": brier(s, y),
            "threshold": cc.DEFAULT_ESCALATE_LO}

    # ---- fit Platt + thresholds on the CALIBRATION split only ----
    a, b = cc.fit_platt([c["raw_llm"] for c in cal], [c["label"] for c in cal])
    cal_pos = [c["raw_llm"] for c in cal if c["label"] == 1]
    cal_neg = [c["raw_llm"] for c in cal if c["label"] == 0]
    separated = bool(cal_pos and cal_neg and min(cal_pos) > max(cal_neg))
    for c in cases:
        c["calibrated"] = cc.apply_platt(c["raw_llm"], a, b)
    lo, hi = select_thresholds([c["calibrated"] for c in cal], [c["label"] for c in cal],
                               max_fpr=args.max_fpr)
    print(f"[fit] platt a={a:.4f} b={b:.4f}  escalate_lo={lo}  deny_hi={hi}"
          f"  calibration_split_separable={separated}")
    if separated:
        print("[fit] NOTE the calibration split is linearly separable, so the threshold "
              "search has no false positives to trade against and the escalate band "
              "collapses to its floor. Treat the bands as a floor, not a tuned "
              "operating point.")

    for name, subset in (("calibration", cal), ("held_out", held), ("full", cases)):
        s = [c["calibrated"] for c in subset]
        y = [c["label"] for c in subset]
        report["gates"].setdefault("llm_calibrated", {})[name] = {
            **confusion(s, y, lo), "brier": brier(s, y), "threshold": lo,
            "deny_band": confusion(s, y, hi)}

    report["fit"] = {"a": a, "b": b, "escalate_lo": lo, "deny_hi": hi,
                     "model": args.model or "(default)", "max_fpr_budget": args.max_fpr,
                     "split_every": args.split_every, "n_cal": len(cal)}
    report["cases"] = [{k: c[k] for k in ("id", "label", "family", "tool", "naive",
                                          "raw_llm", "calibrated") if k in c} for c in cases]

    FIT_PATH.write_text(
        json.dumps(
            {
                "schema_version": cc.CALIBRATION_SCHEMA_VERSION,
                "threshold_version": cc.THRESHOLD_VERSION,
                "fit": report["fit"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    # ---- the table ----
    print()
    print(f"{'gate':<24} {'split':<12} {'recall':>7} {'FPR':>7} {'Brier':>8}  tp/fn/fp/tn")
    for gate in ("naive_keyword", "llm_raw_uncalibrated", "llm_calibrated"):
        for split in ("held_out", "full"):
            m = report["gates"][gate][split]
            print(f"{gate:<24} {split:<12} {m['recall']:>7.3f} {m['fpr']:>7.3f} "
                  f"{m['brier']:>8.4f}  {m['tp']}/{m['fn']}/{m['fp']}/{m['tn']}")
    print()
    print(f"[wrote] {FIT_PATH}")
    print(f"[wrote] {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
