"""One-shot smoke probe: build the build_demo Shield and run a benign
account-read request through chat_guarded_build_demo end-to-end.

Validates that:
- guardrails.build_demo.yaml loads via the same path agent.py uses
- the agent can complete a benign request without any gate firing
- the populator + Stage 2 plumbing doesn't throw on null variables before
  their first write (the `default:` quirk I documented)
"""
from __future__ import annotations

import os
import sys
import traceback

# Ensure we hit gpt-4o-mini for the smoke (fast, cheap, doesn't require
# any model with prompt-shields enabled).
os.environ.setdefault("AGENT_MODEL", "gpt-4o-mini")

from examples.bank_manager_agent_shield.agent import (  # noqa: E402
    chat_guarded_build_demo,
    chat_unguarded,
)


def main() -> int:
    probes = [
        ("benign read", "Show me the balance on ACC-1001 please."),
    ]
    fail = 0
    for label, msg in probes:
        for variant_label, fn in [
            ("UNGUARDED", chat_unguarded),
            ("GUARDED", chat_guarded_build_demo),
        ]:
            print(f"\n=== {variant_label} :: {label} ===")
            try:
                out = fn(msg)
                head = (out or "")[:600].replace("\n", "\\n")
                print(f"OK len={len(out or '')} head={head}")
            except Exception:  # noqa: BLE001
                fail += 1
                traceback.print_exc()
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
