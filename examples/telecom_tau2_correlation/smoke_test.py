#!/usr/bin/env python3
"""Smoke-test each endpoint defined in models.yaml.

Sends a single completion request per endpoint to verify connectivity,
authentication, and model availability.

Usage:
    python smoke_test.py                  # test all endpoints
    python smoke_test.py westus2          # test one endpoint
    python smoke_test.py --list           # show endpoint config
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()  # pick up .env from repo root

SCRIPT_DIR = Path(__file__).resolve().parent
MODELS_YAML = SCRIPT_DIR / "models.yaml"

# ── helpers ──────────────────────────────────────────────────────────

def load_config() -> dict:
    return yaml.safe_load(MODELS_YAML.read_text())


def pick_test_model(config: dict, endpoint_key: str) -> str | None:
    """Return the first model assigned to this endpoint (cheapest to call)."""
    for m in config.get("models", []):
        if m.get("endpoint", "default") == endpoint_key:
            return m["name"]
    return None


def resolve_vars(config: dict, endpoint_key: str) -> tuple[str, str]:
    """Return (base_env_var, key_env_var) for an endpoint."""
    endpoints = config.get("endpoints", {})
    api_keys = config.get("api_keys", {})
    base_var = endpoints.get(endpoint_key, endpoints.get("default", "AZURE_API_BASE"))
    key_var = api_keys.get(endpoint_key, api_keys.get("default", "AZURE_API_KEY"))
    return base_var, key_var


def show_config(config: dict) -> None:
    """Print endpoint configuration and env var status."""
    endpoints = config.get("endpoints", {})
    api_keys = config.get("api_keys", {})
    print("\nEndpoint configuration (from models.yaml):\n")
    for key in endpoints:
        base_var, key_var = resolve_vars(config, key)
        base_val = os.environ.get(base_var)
        key_val = os.environ.get(key_var)
        model = pick_test_model(config, key) or "(no models)"
        base_status = base_val[:40] + "..." if base_val and len(base_val) > 40 else (base_val or "NOT SET")
        key_status = key_var + "=****" if key_val else key_var + "=NOT SET"
        print(f"  [{key}]")
        print(f"    base : {base_var} = {base_status}")
        print(f"    key  : {key_status}")
        print(f"    model: {model}")
        print()


def test_endpoint(config: dict, endpoint_key: str) -> bool:
    """Send a single completion to verify the endpoint. Returns True on success."""
    try:
        import litellm
    except ImportError:
        print("ERROR: litellm not installed. Run: pip install litellm")
        return False

    base_var, key_var = resolve_vars(config, endpoint_key)
    base_url = os.environ.get(base_var)
    api_key = os.environ.get(key_var)

    if not base_url:
        print(f"  SKIP — {base_var} not set")
        return False
    if not api_key:
        print(f"  SKIP — {key_var} not set")
        return False

    model = pick_test_model(config, endpoint_key)
    if not model:
        print(f"  SKIP — no models assigned to endpoint '{endpoint_key}'")
        return False

    print(f"  model : {model}")
    print(f"  base  : {base_url[:50]}...")
    print(f"  key   : {key_var}=****")
    print(f"  calling... ", end="", flush=True)

    try:
        resp = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            api_base=base_url,
            api_key=api_key,
            max_tokens=5,
            temperature=0,
        )
        text = resp.choices[0].message.content.strip()
        print(f"OK  (response: {text!r})")
        return True
    except Exception as e:
        print(f"FAILED")
        print(f"  error: {e}")
        return False


# ── main ─────────────────────────────────────────────────────────────

def main() -> None:
    config = load_config()
    endpoints = config.get("endpoints", {})

    if "--list" in sys.argv:
        show_config(config)
        return

    # Filter to specific endpoints if named on CLI
    targets = [a for a in sys.argv[1:] if not a.startswith("-")]
    if targets:
        test_keys = [k for k in targets if k in endpoints]
        unknown = [k for k in targets if k not in endpoints]
        if unknown:
            print(f"Unknown endpoint(s): {', '.join(unknown)}")
            print(f"Available: {', '.join(endpoints.keys())}")
            sys.exit(1)
    else:
        test_keys = list(endpoints.keys())

    print(f"\nSmoke-testing {len(test_keys)} endpoint(s)...\n")

    results: dict[str, bool] = {}
    for key in test_keys:
        print(f"── {key} ──")
        results[key] = test_endpoint(config, key)
        print()

    # Summary
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"Results: {passed}/{total} endpoints OK")

    if passed < total:
        failed = [k for k, v in results.items() if not v]
        print(f"Failed/skipped: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
