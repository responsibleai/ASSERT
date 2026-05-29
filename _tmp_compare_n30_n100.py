"""Compare n=30 and n=100 variant-A inference + scores side by side."""
import json
import collections
from pathlib import Path

ROOT = Path("artifacts/results/bank-manager-build-demo")
N30 = ROOT / "variant-a-build-demo-unguarded"
N100 = ROOT / "variant-a-build-demo-unguarded-n100"

DIMS = [
    "prompt_injection_via_memo_to_account_viol",
    "fabricated_transfer_id_viol",
    "approval_token_replay_viol",
    "policy_violation",
    "overrefusal",
]


def load(p: Path):
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def has_assistant_activity(inf) -> bool:
    for ev in inf.get("events") or []:
        edit = ev.get("edit") or {}
        msg = edit.get("message") or {}
        if msg.get("role") == "assistant":
            content = msg.get("content")
            tool_calls = msg.get("tool_calls")
            if (content and str(content).strip()) or tool_calls:
                return True
        if edit.get("type") in {"tool_call", "tool_result", "add_tool_call", "add_tool_result"}:
            return True
        if ev.get("actor") == "agent":
            return True
    return False


def cell_grid(infs):
    g = collections.Counter()
    for r in infs:
        d = r.get("dimensions") or {}
        g[(d.get("behavior", ""), d.get("failure_mode_axis", ""))] += 1
    return g


def empty_grid(infs):
    cells = collections.defaultdict(lambda: [0, 0])
    for r in infs:
        d = r.get("dimensions") or {}
        key = (d.get("behavior", ""), d.get("failure_mode_axis", ""))
        cells[key][1] += 1
        if not has_assistant_activity(r):
            cells[key][0] += 1
    return cells


def viol_grid(infs, scores, dim_name):
    by_id = {s["test_case_id"]: s for s in scores}
    cells = collections.defaultdict(lambda: [0, 0])
    for r in infs:
        d = r.get("dimensions") or {}
        key = (d.get("behavior", ""), d.get("failure_mode_axis", ""))
        cells[key][1] += 1
        s = by_id.get(r["test_case_id"])
        if not s:
            continue
        verdict = s.get("verdict") or {}
        dims = verdict.get("dimensions") or {}
        if dims.get(dim_name) is True:
            cells[key][0] += 1
    return cells


def overall(infs, scores, dim_name):
    by_id = {s["test_case_id"]: s for s in scores}
    fires = 0
    for r in infs:
        s = by_id.get(r["test_case_id"])
        if not s:
            continue
        verdict = s.get("verdict") or {}
        dims = verdict.get("dimensions") or {}
        if dims.get(dim_name) is True:
            fires += 1
    return fires, len(infs)


def print_table(label, cells):
    behaviors = sorted({b for b, _ in cells})
    fms = sorted({f for _, f in cells})
    print(f"\n{label}")
    print(f"  {'behavior':<55} " + " ".join(f"{f[:18]:>10}" for f in fms))
    for b in behaviors:
        row = []
        for f in fms:
            fires, total = cells.get((b, f), [0, 0])
            row.append(f"{fires}/{total}")
        print(f"  {b[:55]:<55} " + " ".join(f"{x:>10}" for x in row))


n30_inf = load(N30 / "inference_set.jsonl")
n100_inf = load(N100 / "inference_set.jsonl")
n30_sc = load(N30 / "scores.jsonl")
n100_sc = load(N100 / "scores.jsonl")

print("=" * 100)
print("EMPTY-TRANSCRIPT COUNT (no assistant message/tool call)")
print("=" * 100)
e30 = empty_grid(n30_inf)
e100 = empty_grid(n100_inf)
emp30 = sum(c[0] for c in e30.values())
emp100 = sum(c[0] for c in e100.values())
print(f"\nn=30:  {emp30}/{len(n30_inf)} empty transcripts ({emp30/len(n30_inf):.1%})")
print(f"n=100: {emp100}/{len(n100_inf)} empty transcripts ({emp100/len(n100_inf):.1%})")

print()
print("=" * 100)
print("CELL COUNTS (rows per behavior x FM cell)")
print("=" * 100)
g30 = cell_grid(n30_inf)
g100 = cell_grid(n100_inf)
behaviors = sorted({b for b, _ in (list(g30) + list(g100))})
fms_30 = sorted({f for _, f in g30})
fms_100 = sorted({f for _, f in g100})
print("\nn=30 FMs : " + " | ".join(fms_30))
print("n=100 FMs: " + " | ".join(fms_100))
print()
print("n=30 cells:")
print(f"  {'behavior':<55} " + " ".join(f"{f[:18]:>20}" for f in fms_30))
for b in behaviors:
    if all((b, f) not in g30 for f in fms_30):
        continue
    row = " ".join(f"{g30.get((b,f),0):>20}" for f in fms_30)
    print(f"  {b[:55]:<55} {row}")
print("\nn=100 cells:")
print(f"  {'behavior':<55} " + " ".join(f"{f[:18]:>20}" for f in fms_100))
for b in behaviors:
    if all((b, f) not in g100 for f in fms_100):
        continue
    row = " ".join(f"{g100.get((b,f),0):>20}" for f in fms_100)
    print(f"  {b[:55]:<55} {row}")

print()
print("=" * 100)
print("VIOLATION RATES (per dimension, overall and per behavior x FM cell)")
print("=" * 100)
for dim in DIMS:
    f30, t30 = overall(n30_inf, n30_sc, dim)
    f100, t100 = overall(n100_inf, n100_sc, dim)
    print(f"\n{dim}:")
    print(f"  overall n=30:  {f30}/{t30}  ({100*f30/t30:.1f}%)")
    print(f"  overall n=100: {f100}/{t100}  ({100*f100/t100:.1f}%)")
    v30 = viol_grid(n30_inf, n30_sc, dim)
    v100 = viol_grid(n100_inf, n100_sc, dim)
    print_table(f"  n=30 per cell:", v30)
    print_table(f"  n=100 per cell:", v100)
