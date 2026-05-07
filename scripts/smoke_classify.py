"""Smoke-cell classifier — engineering CI helper.

Reads a single cell's `result.json` + `cell.log` and decides the final
verdict. Real p2m bugs block (exit 1); transient external failures
(rate-limit / 5xx / network / content filter) downgrade to warn (exit 0).

Has a `--summarize` mode that aggregates all cell classifications into a
markdown table for the PR summary.

Engineer-owned flesh-out points are flagged with `# TODO(eng)` comments.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

# --- Classification rules -----------------------------------------------
#
# Order matters — first matching rule wins. P2M_BUG check is performed
# AFTER external patterns so a transient that happens to surface a p2m
# stack frame still gets correctly classified as external.
#
# TODO(eng): add fingerprints as we observe them in production. Each
# pattern is a compiled regex matched against the full cell log.

EXTERNAL_PATTERNS: list[tuple[str, str]] = [
    # (classification, regex)
    ("EXTERNAL_RATE_LIMIT", r"(?i)\b(rate[\s_-]?limit|429\s+too\s+many\s+requests|RateLimitError|TPM\s+limit)\b"),
    ("EXTERNAL_5XX",        r"(?i)\b(50[234]\s+(service\s+unavailable|bad\s+gateway|gateway\s+timeout)|ServiceResponseError|InternalServerError)\b"),
    ("EXTERNAL_NETWORK",    r"(?i)\b(ConnectionError|ConnectionResetError|ReadTimeout|gaierror|ClientConnectorError|TLSV1_ALERT)\b"),
    ("EXTERNAL_CONTENT_FILTER", r"(?i)\b(content[_\s-]?filter|ResponsibleAIPolicy|jailbreak.{0,40}detected)\b"),
]

# A frame from p2m itself in the traceback strongly suggests a p2m bug.
P2M_FRAME_RE = re.compile(r'File "[^"]*[/\\]p2m[/\\][^"]+",\s+line\s+\d+,\s+in\s+', re.IGNORECASE)


@dataclass
class Classification:
    label: str
    classification: str  # PASS | EXTERNAL_RATE_LIMIT | EXTERNAL_5XX | EXTERNAL_NETWORK | EXTERNAL_CONTENT_FILTER | P2M_BUG | UNKNOWN
    blocking: bool       # True → cell exit 1, blocks PR
    exit_code: int
    elapsed_s: float
    n_seeds: int | None
    n_scores: int | None
    detail: str          # one-line human-readable reason


def _classify(result: dict, log_text: str) -> Classification:
    label = result.get("label", "?")
    exit_code = result.get("exit_code", -1)
    elapsed = float(result.get("elapsed_s") or 0.0)
    n_seeds = result.get("n_seeds")
    n_scores = result.get("n_scores")

    # Fast path: clean exit + we got scores out → PASS.
    if exit_code == 0 and (n_scores or 0) > 0:
        return Classification(label, "PASS", False, exit_code, elapsed, n_seeds, n_scores,
                              detail=f"{n_scores} scores produced")

    # Anything else: classify by log content.
    for kind, pat in EXTERNAL_PATTERNS:
        m = re.search(pat, log_text)
        if m:
            snippet = m.group(0)[:80]
            return Classification(label, kind, False, exit_code, elapsed, n_seeds, n_scores,
                                  detail=f"matched {kind}: {snippet!r}")

    # Bench against p2m frames in stack traces (last ~8KB of log).
    tail = log_text[-8192:]
    if P2M_FRAME_RE.search(tail):
        return Classification(label, "P2M_BUG", True, exit_code, elapsed, n_seeds, n_scores,
                              detail="exception originated in p2m/")

    if exit_code == 0:
        # Exit 0 but no scores produced — odd, but not an external failure;
        # likely a p2m logic bug that swallowed the error. Block conservatively.
        return Classification(label, "P2M_BUG", True, exit_code, elapsed, n_seeds, n_scores,
                              detail="exit 0 but no scores produced")

    return Classification(label, "UNKNOWN", True, exit_code, elapsed, n_seeds, n_scores,
                          detail=f"exit {exit_code}, no recognized pattern (block conservatively)")


def _classify_one(args: argparse.Namespace) -> int:
    result = json.loads(Path(args.result_json).read_text(encoding="utf-8"))
    log_text = Path(args.log).read_text(encoding="utf-8", errors="replace") if Path(args.log).exists() else ""
    cls = _classify(result, log_text)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(asdict(cls), indent=2), encoding="utf-8")
    icon = "✅" if cls.classification == "PASS" else ("❌" if cls.blocking else "⚠️")
    print(f"{icon} {cls.label}: {cls.classification} — {cls.detail}")
    return 1 if cls.blocking else 0


def _summarize(args: argparse.Namespace) -> int:
    root = Path(args.artifacts_root)
    rows: list[Classification] = []
    for cls_path in sorted(root.glob("*/classification.json")):
        try:
            data = json.loads(cls_path.read_text(encoding="utf-8"))
            rows.append(Classification(**data))
        except Exception as e:  # noqa: BLE001
            print(f"[summarize] skip {cls_path}: {e}", file=sys.stderr)

    blocking = sum(1 for r in rows if r.blocking)
    warn = sum(1 for r in rows if not r.blocking and r.classification != "PASS")
    passed = sum(1 for r in rows if r.classification == "PASS")
    overall = "❌ BLOCK" if blocking else ("⚠️ WARN" if warn else "✅ PASS")

    lines: list[str] = []
    lines.append(f"## 🧪 Engineering Smoke — {overall}")
    lines.append("")
    lines.append(f"**{passed} passed · {warn} warn · {blocking} blocked** "
                 f"({len(rows)} cells total)")
    lines.append("")
    lines.append("| Cell | Verdict | Elapsed | Seeds | Scores | Detail |")
    lines.append("|---|---|---|---|---|---|")
    for r in rows:
        icon = "✅" if r.classification == "PASS" else ("❌" if r.blocking else "⚠️")
        lines.append(
            f"| `{r.label}` | {icon} {r.classification} | {r.elapsed_s:.0f}s "
            f"| {r.n_seeds or '-'} | {r.n_scores or '-'} | {r.detail} |"
        )
    lines.append("")
    if blocking:
        lines.append("> ❌ **Blocking cells require attention** — see uploaded `smoke-*` artifacts for full logs.")
    elif warn:
        lines.append("> ⚠️ **External failures detected** — not blocking, but please re-run if Azure was degraded.")

    out = Path(args.output)
    if str(out) == "-":
        print("\n".join(lines))
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    # Summary job is informational — never block on aggregation; per-cell
    # `_classify_one` already gated.
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_mutually_exclusive_group()
    sub.add_argument("--summarize", action="store_true",
                     help="Aggregate all cell classifications into a PR-summary markdown table.")
    p.add_argument("--result-json", help="(classify mode) per-cell result.json path")
    p.add_argument("--log", help="(classify mode) per-cell log file path")
    p.add_argument("--output", required=True,
                   help="(classify mode) classification.json path; (summarize mode) markdown sink (use - for stdout)")
    p.add_argument("--artifacts-root", default="artifacts/smoke",
                   help="(summarize mode) root containing one subdir per cell")
    args = p.parse_args(argv)

    if args.summarize:
        return _summarize(args)
    if not args.result_json or not args.log:
        p.error("--result-json and --log are required unless --summarize is set")
    return _classify_one(args)


if __name__ == "__main__":
    sys.exit(main())
