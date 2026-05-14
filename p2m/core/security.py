"""Security utilities for the p2m pipeline.

Provides validation helpers for dynamic module loading, URL validation,
credential sanitization, and path safety checks.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
import sys
import warnings
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# Custom warning class for security-related warnings
class P2MSecurityWarning(UserWarning):
    """Warning issued for potentially dangerous operations in p2m."""
    pass

# ── Module import validation ───────────────────────────────────

# Environment variable to bypass callable validation (opt-in to trust)
_TRUST_CALLABLE_ENV = "P2M_TRUST_CALLABLE"

# Patterns that are never allowed in module references
_DANGEROUS_MODULE_PATTERNS = re.compile(
    r"(^|\.)(__pycache__|\.git|node_modules|site-packages)($|\.)"
)


def is_callable_trusted() -> bool:
    """Check if the user has opted into trusting callable imports."""
    return os.environ.get(_TRUST_CALLABLE_ENV, "").lower() in ("1", "true", "yes")


def validate_callable_ref(callable_ref: str, *, config_path: Path | None = None) -> None:
    """Validate a callable reference before dynamic import.

    Raises ValueError if the reference looks dangerous.
    Emits a warning if trust mode is not explicitly enabled.
    """
    if not callable_ref or ":" not in callable_ref:
        raise ValueError(
            f"Invalid callable reference '{callable_ref}': must be in 'module.path:function_name' format"
        )

    module_path, func_name = callable_ref.rsplit(":", 1)

    if not module_path or not func_name:
        raise ValueError(
            f"Invalid callable reference '{callable_ref}': both module path and function name are required"
        )

    if _DANGEROUS_MODULE_PATTERNS.search(module_path):
        raise ValueError(
            f"Callable reference '{callable_ref}' contains a disallowed path segment"
        )

    # Warn if trust mode is not explicitly enabled
    if not is_callable_trusted():
        warnings.warn(
            f"Dynamic import of '{module_path}' from callable ref '{callable_ref}'. "
            f"Set {_TRUST_CALLABLE_ENV}=1 to suppress this warning. "
            f"Only use callable targets from sources you trust.",
            P2MSecurityWarning,
            stacklevel=3,
        )


def validate_module_ref(module_ref: str, *, config_path: Path | None = None) -> None:
    """Validate a tool/connector module reference before dynamic import.

    Raises ValueError if the reference looks dangerous.
    """
    if not module_ref:
        raise ValueError("Module reference must not be empty")

    if _DANGEROUS_MODULE_PATTERNS.search(module_ref):
        raise ValueError(
            f"Module reference '{module_ref}' contains a disallowed path segment"
        )


def validate_sys_path_addition(path: Path, *, config_path: Path | None = None) -> None:
    """Validate that a sys.path addition is scoped to the workspace.

    Only allows paths that are within the config directory or current working directory.
    Raises ValueError for paths outside the expected workspace.
    """
    resolved = path.resolve()
    cwd = Path.cwd().resolve()

    # Allow paths within cwd
    try:
        resolved.relative_to(cwd)
        return
    except ValueError:
        pass

    # Allow paths within config directory
    if config_path is not None:
        config_dir = config_path.parent.resolve()
        try:
            resolved.relative_to(config_dir)
            return
        except ValueError:
            pass

    # Block system/global paths
    blocked_prefixes = [
        Path("/usr/lib"),
        Path("/usr/local/lib"),
        Path(sys.prefix) / "lib",
    ]
    for prefix in blocked_prefixes:
        try:
            resolved.relative_to(prefix)
            raise ValueError(
                f"Refusing to add system path '{resolved}' to sys.path. "
                f"Only workspace-local paths are allowed."
            )
        except ValueError as e:
            if "Refusing" in str(e):
                raise
            continue

    # Warn but allow for other paths (backward compatibility)
    log.warning(
        "Adding path '%s' to sys.path that is outside the workspace. "
        "Consider using paths relative to your config or working directory.",
        resolved,
    )


# ── URL validation (SSRF prevention) ──────────────────────────

_BLOCKED_IP_RANGES = [
    ipaddress.ip_network("127.0.0.0/8"),       # Loopback
    ipaddress.ip_network("10.0.0.0/8"),        # Private
    ipaddress.ip_network("172.16.0.0/12"),     # Private
    ipaddress.ip_network("192.168.0.0/16"),    # Private
    ipaddress.ip_network("169.254.0.0/16"),    # Link-local / cloud metadata
    ipaddress.ip_network("168.63.129.16/32"),  # Azure Wireserver / platform IMDS
    ipaddress.ip_network("::1/128"),           # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),          # IPv6 private
    ipaddress.ip_network("fe80::/10"),         # IPv6 link-local
]

_BLOCKED_HOSTNAMES = {
    "metadata.google.internal",
    "metadata.google.com",
    "169.254.169.254",
    "168.63.129.16",
    "metadata",
}

# Hostnames explicitly allowed for local development (not SSRF rebinding targets)
_LOCAL_DEV_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
}


def validate_endpoint_url(url: str, *, allow_private: bool = False) -> None:
    """Validate an HTTP endpoint URL to prevent SSRF attacks.

    Blocks requests to:
    - Private/internal IP ranges (RFC 1918, link-local, loopback)
    - Cloud metadata endpoints (169.254.169.254, metadata.google.internal)
    - Non-HTTP(S) schemes

    Args:
        url: The URL to validate.
        allow_private: If True, skip private/internal IP checks (for local development).

    Raises ValueError if the URL is potentially dangerous.
    """
    if allow_private:
        return

    # Environment variable override for development/testing
    if os.environ.get("P2M_ALLOW_PRIVATE_ENDPOINTS", "").lower() in ("1", "true", "yes"):
        return

    try:
        parsed = urlparse(url)
    except Exception as e:
        raise ValueError(f"Invalid URL '{url}': {e}") from e

    # Scheme validation
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"URL scheme '{parsed.scheme}' is not allowed. Only http and https are permitted."
        )

    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"URL '{url}' has no hostname")

    # Check blocked hostnames
    if hostname.lower() in _BLOCKED_HOSTNAMES:
        raise ValueError(
            f"URL hostname '{hostname}' is blocked (potential metadata endpoint)"
        )

    # Try to parse as IP address
    try:
        ip = ipaddress.ip_address(hostname)
        for network in _BLOCKED_IP_RANGES:
            if ip in network:
                raise ValueError(
                    f"URL resolves to blocked IP range ({network}): {hostname}"
                )
    except ValueError as e:
        if "blocked" in str(e).lower():
            raise
        # Not an IP literal — resolve hostname and check resulting IPs
        if hostname.lower() not in _LOCAL_DEV_HOSTNAMES:
            _validate_resolved_ips(hostname)


def _validate_resolved_ips(hostname: str) -> None:
    """Resolve a hostname via DNS and validate all returned IPs against blocked ranges.

    Raises ValueError if any resolved IP falls within a blocked range.
    """
    try:
        addrinfo = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        # If DNS resolution fails, allow the request through — it will fail
        # at connection time with a clear error. This avoids false positives
        # for hosts that are only resolvable from certain networks.
        log.debug("DNS resolution failed for '%s'; skipping IP validation", hostname)
        return

    for family, _type, _proto, _canonname, sockaddr in addrinfo:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        for network in _BLOCKED_IP_RANGES:
            if ip in network:
                log.warning(
                    "SSRF protection: hostname '%s' resolves to blocked IP %s (range %s)",
                    hostname,
                    ip_str,
                    network,
                )
                raise ValueError(
                    f"URL hostname '{hostname}' resolves to blocked IP range ({network}): {ip_str}"
                )


# ── Credential sanitization ────────────────────────────────────

_SENSITIVE_KEYS = re.compile(
    r"(api[_-]?key|auth[_-]?token|secret|password|credential|bearer|authorization|"
    r"access[_-]?token|refresh[_-]?token|private[_-]?key|client[_-]?secret)",
    re.IGNORECASE,
)

_REDACTED = "[REDACTED]"


def sanitize_payload(payload: Any, *, depth: int = 0, max_depth: int = 10) -> Any:
    """Recursively sanitize sensitive fields from a payload before writing to artifacts.

    Redacts values for keys matching common credential patterns.
    """
    if depth > max_depth:
        log.warning(
            "sanitize_payload: max depth (%d) exceeded — redacting remaining payload",
            max_depth,
        )
        return "[REDACTED: max depth exceeded]"

    if isinstance(payload, dict):
        sanitized = {}
        for key, value in payload.items():
            if isinstance(key, str) and _SENSITIVE_KEYS.search(key):
                sanitized[key] = _REDACTED
            else:
                sanitized[key] = sanitize_payload(value, depth=depth + 1, max_depth=max_depth)
        return sanitized
    elif isinstance(payload, list):
        return [sanitize_payload(item, depth=depth + 1, max_depth=max_depth) for item in payload]
    elif isinstance(payload, str):
        # Redact Bearer tokens in string values
        if payload.startswith("Bearer ") or payload.startswith("Basic "):
            return _REDACTED
        return payload
    return payload


# ── Dotenv validation ──────────────────────────────────────────

def validate_dotenv_location(*, config_path: Path | None = None) -> bool:
    """Validate that .env file is in the expected project root.

    Returns True if safe, emits a warning and returns False if suspicious.
    """
    cwd = Path.cwd().resolve()
    dotenv_path = cwd / ".env"

    if not dotenv_path.exists():
        return True  # No .env to worry about

    # Check if config_path is provided and .env is in a different directory
    if config_path is not None:
        config_dir = config_path.parent.resolve()
        if cwd != config_dir:
            # .env is being loaded from cwd but config is elsewhere
            warnings.warn(
                f"Loading .env from working directory '{cwd}' which differs from "
                f"config directory '{config_dir}'. Ensure this .env file is trusted. "
                f"Set P2M_NO_DOTENV=1 to disable automatic .env loading.",
                P2MSecurityWarning,
                stacklevel=2,
            )
            return False

    return True


def should_load_dotenv() -> bool:
    """Check if dotenv loading is enabled."""
    return os.environ.get("P2M_NO_DOTENV", "").lower() not in ("1", "true", "yes")
