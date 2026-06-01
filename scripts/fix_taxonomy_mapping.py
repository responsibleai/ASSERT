"""Remap bank_manager_agent_control test_set behaviors to formal taxonomy names.

The systematize stage regenerated taxonomy.json with four formal category
names while test_set.jsonl and downstream artifacts retained the older
descriptive labels. The viewer (viewer/src/lib/server/data.ts) does a
strict taxonomy lookup on dimensions.behavior and 500s on mismatch.

This script:
  1. Builds a {test_case_id -> formal_behavior} map from test_set.jsonl.
  2. Applies it consistently to dimensions.behavior and any top-level
     `behavior` field that holds a descriptive label, across every
     suite-level and per-variant artifact file.

Mapping:
  authorized banking tool use            -> Authorization-gated action handling
  accurate financial statements ...      -> Correct tool use and customer-facing banking help
  confidentiality and social-engineering -> split per case:
      Single account lookup (Calm)        -> Confidential data withholding
      CEO override (Sensitive data ...)   -> Resistance to impersonation and prompt injection
      VIP transfer request (Urgent)       -> Resistance to impersonation and prompt injection
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "examples" / "bank_manager_agent_control" / "results"

SIMPLE_MAP = {
    "authorized banking tool use": "Authorization-gated action handling",
    "accurate financial statements and account state": "Correct tool use and customer-facing banking help",
}

DESCRIPTIVE_LABELS = set(SIMPLE_MAP) | {"confidentiality and social-engineering resistance"}


def classify_social(row: dict) -> str:
    """Split the 'confidentiality and social-engineering resistance' bucket."""
    req = (row.get("dimensions") or {}).get("request_type", "")
    if req == "Single account lookup":
        return "Confidential data withholding"
    # CEO override claim, VIP transfer request, and any other impersonation /
    # jailbreak / pressure-based attempt fall under impersonation/injection.
    return "Resistance to impersonation and prompt injection"


def build_mapping() -> dict[str, str]:
    mapping: dict[str, str] = {}
    test_set = ROOT / "test_set.jsonl"
    with test_set.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            tcid = row["test_case_id"]
            old = (row.get("dimensions") or {}).get("behavior", "")
            if old in SIMPLE_MAP:
                mapping[tcid] = SIMPLE_MAP[old]
            elif old == "confidentiality and social-engineering resistance":
                mapping[tcid] = classify_social(row)
            else:
                mapping[tcid] = old
    return mapping


def rewrite_row(row: dict, mapping: dict[str, str]) -> None:
    tcid = row.get("test_case_id")
    if not tcid or tcid not in mapping:
        return
    new_val = mapping[tcid]
    dims = row.get("dimensions")
    if isinstance(dims, dict) and dims.get("behavior") in DESCRIPTIVE_LABELS:
        dims["behavior"] = new_val
    top = row.get("behavior")
    if isinstance(top, str) and top in DESCRIPTIVE_LABELS:
        row["behavior"] = new_val


def rewrite_jsonl(path: Path, mapping: dict[str, str]) -> int:
    if not path.exists():
        return 0
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    for r in rows:
        rewrite_row(r, mapping)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return len(rows)


def rewrite_json_list(path: Path, mapping: dict[str, str]) -> int:
    if not path.exists():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        for r in data:
            if isinstance(r, dict):
                rewrite_row(r, mapping)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(data) if isinstance(data, list) else 0


def main() -> None:
    mapping = build_mapping()
    counts = Counter(mapping.values())
    print("Per-category test_case counts (from test_set.jsonl):")
    for k, v in sorted(counts.items()):
        print(f"  {v:3d}  {k}")

    summary: list[tuple[str, int]] = []
    summary.append(("test_set.jsonl", rewrite_jsonl(ROOT / "test_set.jsonl", mapping)))

    for variant in ("variant-a-unguarded-n100", "variant-e-guarded-acs-n100"):
        vdir = ROOT / variant
        summary.append((f"{variant}/inference_set.jsonl", rewrite_jsonl(vdir / "inference_set.jsonl", mapping)))
        summary.append((f"{variant}/scores.jsonl", rewrite_jsonl(vdir / "scores.jsonl", mapping)))
        summary.append((f"{variant}/.viewer/viewer_prompt_rows.json", rewrite_json_list(vdir / ".viewer" / "viewer_prompt_rows.json", mapping)))
        summary.append((f"{variant}/.viewer/viewer_audit_rows.json", rewrite_json_list(vdir / ".viewer" / "viewer_audit_rows.json", mapping)))

    print("\nFiles patched (row counts):")
    for name, n in summary:
        print(f"  {n:4d}  {name}")


if __name__ == "__main__":
    main()
