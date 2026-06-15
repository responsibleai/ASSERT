#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Deterministic review-request escalation for the maintainer-assist pattern.

Runs on an always-on host (a scheduled GitHub Action — see
``.github/workflows/review-escalation.yml``) so the wall-clock escalation
windows fire even when the maintainer is away, which is exactly the situation
the escalation is for. This is the deterministic half of the dev-maintainer
agent: CODEOWNERS-based review routing. It does NOT run the LLM audit.

For each open PR it applies the windows documented in ``AGENTS.md``:

    < 24h                              observe only
    >= 24h, no reviewer requested      request one CODEOWNER (narrow write #2)
    >= 72h, requested but no response   request a second CODEOWNER
    >= 7d, still no response            request the fallback admin (last resort)

Routing rules (also from ``AGENTS.md``):
  1. Read effective CODEOWNERS for the PR's changed paths (last match wins).
  2. Exclude the PR author.
  3. Exclude owners whose GitHub user status is "busy" / OOO (best-effort).
  4. Exclude the fallback admin (catch-all owner) unless no one else is left.
  5. Prefer the owner covering the most changed paths; tie-break alphabetically.

The script shells out to the `gh` CLI for all GitHub access, so it works the
same locally (maintainer's `gh` auth) and in CI (`GH_TOKEN` / `GITHUB_TOKEN`).
Use ``--dry-run`` to print decisions without requesting any reviewers.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# The catch-all owner in CODEOWNERS is the fallback admin / reviewer of last
# resort; only request them when no other eligible owner remains.
FALLBACK_LOGIN = "changliu2"

WINDOW_24H = 24
WINDOW_72H = 72
WINDOW_7D = 24 * 7


def gh(*args: str, check: bool = True) -> str:
    """Run a gh command and return stdout."""
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def gh_json(*args: str):
    return json.loads(gh(*args) or "null")


# ── CODEOWNERS ────────────────────────────────────────────────


@dataclass
class CodeownersRule:
    pattern: str
    owners: list[str]


def parse_codeowners(path: Path) -> list[CodeownersRule]:
    rules: list[CodeownersRule] = []
    if not path.exists():
        return rules
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        pattern, owners = parts[0], [o.lstrip("@") for o in parts[1:] if o.startswith("@")]
        if owners:
            rules.append(CodeownersRule(pattern=pattern, owners=owners))
    return rules


def _pattern_matches(pattern: str, file_path: str) -> bool:
    """Approximate GitHub CODEOWNERS matching for the patterns this repo uses.

    Supports: `*` (everything), `*.ext` (basename suffix at any depth), and
    `dir/` (anything under a directory prefix), plus literal path prefixes.
    """
    if pattern == "*":
        return True
    if pattern.startswith("*."):  # e.g. *.md — match by extension at any depth
        return file_path.rsplit("/", 1)[-1].endswith(pattern[1:])
    normalized = pattern.lstrip("/")
    if normalized.endswith("/"):  # directory prefix
        return file_path.startswith(normalized)
    # Literal file or path prefix.
    return file_path == normalized or file_path.startswith(normalized + "/")


def effective_owners(file_path: str, rules: list[CodeownersRule]) -> list[str]:
    """Return the owners of the LAST matching rule (GitHub semantics)."""
    owners: list[str] = []
    for rule in rules:
        if _pattern_matches(rule.pattern, file_path):
            owners = rule.owners
    return owners


# ── PR evaluation ─────────────────────────────────────────────


@dataclass
class Decision:
    pr: int
    title: str
    author: str
    age_hours: float
    action: str  # "observe" | "request" | "request-second" | "fallback" | "noop"
    candidates: list[str] = field(default_factory=list)
    chosen: list[str] = field(default_factory=list)
    routing_preview: list[str] = field(default_factory=list)
    reason: str = ""


def _hours_since(iso_ts: str) -> float:
    ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0


def _is_ooo(login: str) -> bool:
    """Best-effort: is the user's GitHub status busy / OOO? Never blocks on error."""
    try:
        data = gh_json(
            "api", "graphql", "-f",
            f'query={{user(login:"{login}"){{status{{indicatesLimitedAvailability message}}}}}}',
        )
        status = (((data or {}).get("data") or {}).get("user") or {}).get("status")
        return bool(status and status.get("indicatesLimitedAvailability"))
    except Exception:
        return False


def _rank_owners(candidates_by_file: dict[str, list[str]]) -> list[str]:
    """Order owners by how many changed files they cover; tie-break alphabetically."""
    coverage: dict[str, int] = {}
    for owners in candidates_by_file.values():
        for o in owners:
            coverage[o] = coverage.get(o, 0) + 1
    return sorted(coverage, key=lambda o: (-coverage[o], o))


def _safe_fallback(author: str, requested: set[str], reviewed_by: set[str]) -> str | None:
    """The fallback admin, or None when requesting them would be wrong.

    Honors routing rule #1 (never request the PR author): if the fallback admin
    *is* the author, or has already been requested or has already reviewed,
    return None so the caller escalates for manual handling instead of pinging
    the author / re-pinging the same person.
    """
    if FALLBACK_LOGIN == author:
        return None
    if FALLBACK_LOGIN in requested or FALLBACK_LOGIN in reviewed_by:
        return None
    return FALLBACK_LOGIN


def evaluate_pr(
    repo: str,
    pr: dict,
    rules: list[CodeownersRule],
    min_age_hours: float,
) -> Decision:
    number = pr["number"]
    author = (pr.get("author") or {}).get("login", "")
    age = _hours_since(pr["createdAt"])

    files = [f["path"] for f in pr.get("files", [])]
    per_file = {f: effective_owners(f, rules) for f in files}

    requested = {r.get("login") for r in pr.get("reviewRequests", []) if r.get("login")}
    reviewed_by = {
        rv.get("author", {}).get("login")
        for rv in pr.get("reviews", [])
        if rv.get("author") and rv.get("state") in {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"}
    }
    reviewed_by.discard(author)

    ranked = _rank_owners(per_file)
    # Pure routing: who owns this PR (ranked), author excluded. Shown for
    # transparency even when everyone is already requested.
    routing_preview = [o for o in ranked if o != author]
    # Eligible = ranked owners minus author, minus already-requested, minus OOO.
    eligible = [
        o for o in ranked
        if o != author and o not in requested and not _is_ooo(o)
    ]
    non_fallback = [o for o in eligible if o != FALLBACK_LOGIN]
    # Prefer specific owners; fall back to the catch-all only if nobody else.
    pool = non_fallback or eligible

    d = Decision(pr=number, title=pr.get("title", ""), author=author,
                 age_hours=round(age, 1), action="noop", candidates=pool,
                 routing_preview=routing_preview)

    if not files:
        d.reason = "no changed files"
        return d
    if age < min_age_hours:
        d.action = "observe"
        d.reason = f"age {age:.1f}h < {min_age_hours}h threshold"
        return d

    has_reviewed = bool(reviewed_by)
    if has_reviewed:
        d.action = "observe"
        d.reason = "a reviewer has already responded"
        return d

    # Cascade by severity. `pool` already excludes the author, OOO owners, and
    # anyone already requested, so no branch can ever target the author.
    if not requested:
        # No reviewer ever requested → assign the first eligible owner (the
        # documented 24h action), at any age past the first window.
        if pool:
            d.action, d.chosen = "request", [pool[0]]
            d.reason = "no reviewer requested; assigning the first eligible owner"
        else:
            fb = _safe_fallback(author, requested, reviewed_by)
            if fb:
                d.action, d.chosen = "fallback", [fb]
                d.reason = "no eligible owner; fallback admin (last resort)"
            else:
                d.action = "manual"
                d.reason = "no eligible owner and fallback would be the author — manual escalation needed"
    elif age >= WINDOW_7D:
        # Requested but silent for 7d+ → fallback admin, the reviewer of last resort.
        fb = _safe_fallback(author, requested, reviewed_by)
        if fb:
            d.action, d.chosen = "fallback", [fb]
            d.reason = "7d+ no response; fallback admin (last resort)"
        elif pool:
            # Fallback is the author or already pinged → widen to another owner.
            d.action, d.chosen = "request-second", [pool[0]]
            d.reason = "7d+ no response; fallback unavailable, widening to another owner"
        else:
            d.action = "manual"
            d.reason = "7d+ no response; fallback is the author and no other owner — manual escalation needed"
    elif age >= WINDOW_72H:
        # Requested but silent for 72h+ → add a second *non-fallback* owner from
        # the same path. The fallback admin is reserved for the 7d last resort,
        # so we do not pull them in early here.
        if non_fallback:
            d.action, d.chosen = "request-second", [non_fallback[0]]
            d.reason = "72h+ requested reviewer non-responsive; adding a second owner"
        else:
            d.action = "observe"
            d.reason = "72h+ but no additional non-fallback owner; awaiting 7d fallback"
    else:
        d.action = "observe"
        d.reason = "reviewer requested; within the response window"
    return d


def request_reviewers(repo: str, pr: int, logins: list[str]) -> None:
    if not logins:
        return
    args = ["pr", "edit", str(pr), "--repo", repo]
    for login in logins:
        args += ["--add-reviewer", login]
    gh(*args)


# ── main ──────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic CODEOWNERS review escalation.")
    ap.add_argument("--repo", default="responsibleai/ASSERT")
    ap.add_argument("--pr", type=int, default=None, help="Evaluate one PR (else all open).")
    ap.add_argument("--min-age-hours", type=float, default=float(WINDOW_24H),
                    help="Override the first escalation window (use 0 to test on a fresh PR).")
    ap.add_argument("--codeowners", default=".github/CODEOWNERS")
    ap.add_argument("--dry-run", action="store_true", help="Print decisions; request no reviewers.")
    args = ap.parse_args()

    rules = parse_codeowners(Path(args.codeowners))
    if not rules:
        print(f"::warning::no CODEOWNERS rules parsed from {args.codeowners}", file=sys.stderr)

    fields = "number,title,author,createdAt,files,reviewRequests,reviews"
    if args.pr is not None:
        prs = [gh_json("pr", "view", str(args.pr), "--repo", args.repo, "--json", fields)]
    else:
        prs = gh_json("pr", "list", "--repo", args.repo, "--state", "open",
                      "--limit", "100", "--json", fields) or []

    exit_code = 0
    for pr in prs:
        d = evaluate_pr(args.repo, pr, rules, args.min_age_hours)
        tag = "DRY-RUN" if args.dry_run else "LIVE"
        print(f"[{tag}] PR #{d.pr} ({d.age_hours}h) by @{d.author}: {d.action} "
              f"-> {('@' + ', @'.join(d.chosen)) if d.chosen else '(none)'} | {d.reason}")
        if d.routing_preview:
            print(f"         routing (ranked owners): {', '.join('@' + c for c in d.routing_preview)}")
        if d.candidates and d.candidates != d.routing_preview:
            print(f"         eligible now (not yet requested): {', '.join('@' + c for c in d.candidates)}")
        if d.action == "manual":
            print(f"::warning::PR #{d.pr} needs manual escalation: {d.reason}")
        if d.action in {"request", "request-second", "fallback"} and d.chosen and not args.dry_run:
            try:
                request_reviewers(args.repo, d.pr, d.chosen)
                print(f"         requested review from {', '.join('@' + c for c in d.chosen)}")
            except Exception as exc:  # noqa: BLE001
                print(f"::error::failed to request reviewers on #{d.pr}: {exc}", file=sys.stderr)
                exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
