"""Install the reviewed 120-case coercion fixture into the ASSERT suite cache.

The published comparison uses one frozen prompt dataset across all three arms.
This script copies that curated fixture into the suite-level ``test_set.jsonl``
location that the disabled ``test_set`` stages in the three powered configs
expect. It never reads credentials or invokes a model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = Path(__file__).resolve().parents[1]
FIXTURE = EXAMPLE / "fixtures" / "coercion_powered_120.jsonl"
LABELS = EXAMPLE / "fixtures" / "coercion_powered_120_labels.json"
EXPECTED_SHA256 = "1f314b96e5ea372787e9b0481990a33ce45c920f74f63eb9cf70238d773260d3"
SUITE = REPO / "artifacts" / "results" / "bank-manager-coercion-powered-120"
TARGET = SUITE / "test_set.jsonl"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace a different test_set.jsonl already present in the suite.",
    )
    args = parser.parse_args()

    fixture_hash = sha256(FIXTURE)
    if fixture_hash != EXPECTED_SHA256:
        raise SystemExit(
            f"fixture hash mismatch: expected {EXPECTED_SHA256}, got {fixture_hash}"
        )

    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    counts = Counter(row["final_label"] for row in labels)
    expected_counts = {
        "coercive": 60,
        "legit_evidenced": 30,
        "legit_routine": 30,
    }
    if dict(counts) != expected_counts:
        raise SystemExit(f"label balance mismatch: {dict(counts)}")

    if TARGET.exists():
        target_hash = sha256(TARGET)
        if target_hash == fixture_hash:
            print(f"already installed: {TARGET}")
            return 0
        if not args.force:
            raise SystemExit(
                f"{TARGET} already exists with a different hash; rerun with --force"
            )

    SUITE.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE, TARGET)
    print(f"installed {len(labels)} reviewed prompts at {TARGET}")
    print(
        "labels: coercive=60, legitimate-with-evidence=30, "
        "legitimate-routine=30"
    )
    print(f"sha256: {fixture_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
