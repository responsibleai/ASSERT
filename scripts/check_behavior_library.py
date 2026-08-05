#!/usr/bin/env python3
"""Guard the behavior library: atomicity, and parity with the spec references.

Two failure modes this prevents.

**Bundling.** `docs/config/best-practices.md` section 8.D requires *atomic*
behaviors -- narrow enough to be tested and judged on their own. A preset that
bundles several mechanisms produces a dataset mixing those mechanisms, and the
resulting metrics cannot be attributed to any single behavioral claim. The
sharpest objective signal is a preset whose description covers behaviors that
already exist as their own presets: that is provable bundling, not a judgement
call.

**Drift.** `examples/behavior_specs/*.md` and `assert_ai/library/behaviors/*.yaml`
hold the same prose in two formats. Only the YAML ships in the wheel. Without a
check they diverge silently, and pip users get whichever half was updated.

Run: python scripts/check_behavior_library.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "assert_ai" / "library" / "behaviors"
SCENARIOS = ROOT / "assert_ai" / "library" / "scenarios"
SPECS = ROOT / "examples" / "behavior_specs"

# Application scenarios, not atomic behaviors. Tracked separately so the rule
# stays honest rather than being silently weakened for them.
SCENARIO_KIND = "scenario"

problems: list[str] = []


def fail(where: str, msg: str) -> None:
    problems.append(f"{where}: {msg}")


def words(text: str) -> list[str]:
    """Wrapping-insensitive token stream.

    The .md files are unwrapped; the YAML descriptions hard-wrap at ~65 chars.
    Comparing lines reports identical prose as ~5% similar.
    """
    text = re.sub(r"^#+\s*", "", text, flags=re.M)
    text = re.sub(r"^[-*]\s+", "", text, flags=re.M)
    text = text.replace("\u2014", "-").replace("\u2019", "'")
    return re.sub(r"\s+", " ", text).strip().lower().split()


def load(path: Path) -> dict:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        fail(path.name, f"invalid YAML: {exc}")
        return {}


def main() -> int:
    presets = {p.stem: load(p) for p in sorted(LIB.glob("*.yaml"))}
    if not presets:
        print("no presets found")
        return 1

    behaviors = {n: d for n, d in presets.items() if d.get("kind") != SCENARIO_KIND}
    scenarios = {p.stem: load(p) for p in sorted(SCENARIOS.glob("*.yaml"))}

    # -- 1. atomicity ------------------------------------------------------
    for name, doc in sorted(behaviors.items()):
        desc = doc.get("description") or ""
        if not desc:
            fail(name, "no description")
            continue

        # Provable bundling: names another preset's behavior.
        others = [
            o for o in behaviors
            if o != name and re.search(rf"\b{re.escape(o.replace('_', ' '))}\b", desc, re.I)
        ]
        if others:
            fail(name, f"bundles other presets ({', '.join(sorted(others))}) -- best-practices 8.D wants atomic behaviors")

        # Multiple '<category> failures' sections is the other bundling shape.
        cats = re.findall(r"^##\s+(.+?)\s+failures?\s*$", desc, flags=re.M | re.I)
        if len(cats) > 1:
            fail(name, f"{len(cats)} failure categories in one preset ({', '.join(cats)}) -- split them")

        # A context/domain spec wearing kind: behavior.
        if re.search(r"^##\s+(Role|Domain Basics|Operational Procedures)\s*$", desc, flags=re.M | re.I):
            fail(name, "reads as an application/domain spec, not a behavior -- belongs in context: or kind: scenario")

    # -- 2. scenario shape -------------------------------------------------
    for name, doc in sorted(scenarios.items()):
        if doc.get("kind") != SCENARIO_KIND:
            fail(name, f"scenario file has kind={doc.get('kind')!r}, expected {SCENARIO_KIND!r}")

        if doc.get("description"):
            fail(name, "scenario must not carry behavior-shaped description:; put app details in context:")

        context = doc.get("context")
        if not isinstance(context, str) or not context.strip():
            fail(name, "scenario must have non-empty context:")
        elif re.search(r"^##\s+.+?\s+failures?\s*$", context, flags=re.M | re.I):
            fail(name, "scenario context must not contain behavior failure sections")

        refs = doc.get("behaviors")
        if not isinstance(refs, list) or not refs:
            fail(name, "scenario must list applicable atomic behavior presets in behaviors:")
            continue
        for ref in refs:
            if not isinstance(ref, str) or not ref:
                fail(name, f"scenario behavior reference must be a non-empty string, got {ref!r}")
            elif ref not in behaviors:
                fail(name, f"scenario references unknown behavior preset {ref!r}")

    # -- 3. parity with the spec references --------------------------------
    # `words()` already normalizes the only expected sources of difference
    # (heading markers, bullet markers, hard-wrapping, unicode dashes/quotes,
    # whitespace, case). Once that normalization is applied, an exact match
    # is achievable for genuinely identical prose -- any remaining difference
    # is real content drift, not formatting noise, so we require an exact
    # match rather than tolerating a similarity band. A fuzzy threshold here
    # would let a changed sentence in a long spec through silently.
    #
    # This check is a hard requirement, not best-effort: if the spec
    # reference directory is missing, that is a parity failure to surface
    # loudly, not a reason to skip the check.
    if not SPECS.is_dir():
        fail("library", f"{SPECS.relative_to(ROOT).as_posix()} is missing -- parity between the pip-shipped "
                         "library presets and their spec references cannot be verified")
    else:
        md = {p.stem: p for p in SPECS.glob("*.md") if p.stem != "README"}
        for name, doc in sorted(behaviors.items()):
            path = md.get(name)
            if path is None:
                fail(name, f"library preset has no {SPECS.relative_to(ROOT).as_posix()} reference")
                continue
            a, b = words(path.read_text(encoding="utf-8")), words(doc.get("description") or "")
            if a != b:
                import difflib
                r = difflib.SequenceMatcher(None, a, b).ratio()
                fail(name, f"library yaml and spec md have drifted (exact match required after "
                           f"wrap/format normalization; similarity {r:.0%})")
        for name, path in sorted(md.items()):
            doc = presets.get(name)
            if doc is None:
                fail(name, f"{path.relative_to(ROOT).as_posix()} has no library preset -- pip users cannot see it")
                continue
            a, b = words(path.read_text(encoding="utf-8")), words(doc.get("description") or "")
            if a != b:
                import difflib
                r = difflib.SequenceMatcher(None, a, b).ratio()
                fail(name, f"spec md and library yaml have drifted (exact match required after "
                           f"wrap/format normalization; similarity {r:.0%})")

    print(f"{len(behaviors) + len(scenarios)} presets ({len(behaviors)} behaviors, {len(scenarios)} scenarios)")
    if problems:
        print(f"\n{len(problems)} problem(s):")
        for p in problems:
            print("  -", p)
        return 1
    print("behavior library OK: atomic, and in parity with examples/behavior_specs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
