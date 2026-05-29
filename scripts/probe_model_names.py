"""Quick probe: confirm Azure Foundry deployment names for new models.

Calls each candidate name via the same LiteLLM path the pipeline uses and
reports HTTP/transport success — does NOT validate output content.

Usage:
    python scripts\probe_model_names.py
"""

from __future__ import annotations

import os
import sys

# Ensure model_client patching runs (drop_params=True etc.)
from p2m.core import model_client  # noqa: F401  # side-effect import
import litellm

CANDIDATES = ["gpt-4.1-mini", "gpt-4.1", "gpt-5"]


def probe(deployment: str) -> tuple[bool, str]:
    """Return (ok, detail)."""
    try:
        resp = litellm.completion(
            model=f"azure/{deployment}",
            messages=[{"role": "user", "content": "Reply with the single word PONG."}],
            max_tokens=10,
            num_retries=0,
        )
        text = (resp.choices[0].message.content or "").strip()
        return True, f"OK ({text!r})"
    except Exception as exc:  # noqa: BLE001 — diagnostic probe
        # Strip the long URL noise from Azure errors
        msg = str(exc)
        if len(msg) > 240:
            msg = msg[:240] + "..."
        return False, f"FAIL ({type(exc).__name__}: {msg})"


def main() -> int:
    if not os.getenv("AZURE_API_BASE") or not os.getenv("AZURE_API_KEY"):
        print("ERROR: AZURE_API_BASE and AZURE_API_KEY must be set", file=sys.stderr)
        return 2
    print(f"Azure base: {os.environ['AZURE_API_BASE']}")
    print()
    max_len = max(len(c) for c in CANDIDATES)
    any_fail = False
    for name in CANDIDATES:
        ok, detail = probe(name)
        status = "PASS" if ok else "FAIL"
        print(f"  {name.ljust(max_len)}  {status}  {detail}")
        if not ok:
            any_fail = True
    return 1 if any_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
