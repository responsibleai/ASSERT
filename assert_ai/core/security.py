# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Security utilities for the ASSERT pipeline.

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
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# ── Module import validation ───────────────────────────────────

# Patterns that are never allowed in module references
_DANGEROUS_MODULE_PATTERNS = re.compile(
    r"(^|\.)(__pycache__|\.git|node_modules|site-packages)($|\.)"
)


def sanitize_callable_ref(callable_ref: str, *, config_path: Path | None = None) -> None:
    """Reject obviously-wrong callable references before dynamic import.

    **This is a hygiene filter, not a security boundary.** It checks the shape of
    the reference and rejects four path substrings. Any other importable module
    named here will be imported and executed with the privileges of the ASSERT
    process. A configuration file that can set ``target.callable`` is therefore
    equivalent to arbitrary code execution, and must be trusted accordingly.

    Raises ValueError if the reference is malformed or contains disallowed path segments.
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


def sanitize_module_ref(module_ref: str, *, config_path: Path | None = None) -> None:
    """Reject obviously-wrong tool/connector module references before dynamic import.

    **This is a hygiene filter, not a security boundary.** See
    :func:`sanitize_callable_ref`: anything not matching the four disallowed path
    substrings is imported and executed.

    Raises ValueError if the reference looks dangerous.
    """
    if not module_ref:
        raise ValueError("Module reference must not be empty")

    if _DANGEROUS_MODULE_PATTERNS.search(module_ref):
        raise ValueError(
            f"Module reference '{module_ref}' contains a disallowed path segment"
        )


# Deprecated aliases. The previous names read as though the reference had been
# validated against a policy, which is how a hygiene filter comes to be relied on
# as a control. Kept for one release so external callers do not break.
validate_callable_ref = sanitize_callable_ref
validate_module_ref = sanitize_module_ref


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
            resolved.relative_to(prefix.resolve())
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


def env_flag(name: str) -> bool:
    """Return True when environment variable ``name`` is set to a truthy value."""
    return os.environ.get(name, "").lower() in ("1", "true", "yes")


def _is_loopback_host(hostname: str) -> bool:
    """Return True when ``hostname`` names the local machine without a DNS lookup."""
    if hostname.lower() in _LOCAL_DEV_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _is_plaintext_permitted(hostname: str) -> bool:
    """Return True when plaintext HTTP is acceptable for ``hostname``.

    Loopback traffic never leaves the machine, so TLS adds no confidentiality
    there. Everything else requires an explicit operator opt-out.
    """
    return _is_loopback_host(hostname) or env_flag("ASSERT_ALLOW_PLAINTEXT_HTTP")


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
    if os.environ.get("ASSERT_ALLOW_PRIVATE_ENDPOINTS", "").lower() in ("1", "true", "yes"):
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

    # Transport security, checked last so that an SSRF verdict on a blocked host
    # is reported as such. Plaintext HTTP is permitted only to loopback, where the
    # traffic never traverses a network, or behind an explicit opt-out. Otherwise
    # the whole evaluation — prompts, responses, and any bearer token — is
    # readable by anything on the path.
    if parsed.scheme == "http" and not _is_plaintext_permitted(hostname):
        raise ValueError(
            f"Endpoint '{url}' uses plaintext HTTP. Use https, or set "
            "ASSERT_ALLOW_PLAINTEXT_HTTP=1 to allow plaintext for a local test server."
        )


def _validate_resolved_ips(hostname: str) -> None:
    """Resolve a hostname via DNS and validate all returned IPs against blocked ranges.

    Fails closed: a hostname that cannot be resolved is rejected rather than
    allowed through. Letting an unresolvable name pass means the guard is
    skipped entirely whenever an attacker can make resolution fail here but
    succeed in the HTTP client.

    Set ``ASSERT_ALLOW_UNRESOLVABLE_ENDPOINTS=1`` on split-horizon networks where
    the validating process genuinely cannot resolve a legitimate internal name.

    Note: this check remains time-of-check/time-of-use. The HTTP client resolves
    the name again when it connects, and a short-TTL record can change between
    the two lookups. Closing that gap requires pinning the validated address at
    the transport layer, which is not done here.

    Raises ValueError if any resolved IP falls within a blocked range.
    """
    try:
        addrinfo = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        if env_flag("ASSERT_ALLOW_UNRESOLVABLE_ENDPOINTS"):
            log.warning(
                "DNS resolution failed for '%s'; allowed by "
                "ASSERT_ALLOW_UNRESOLVABLE_ENDPOINTS. SSRF checks did not run.",
                hostname,
            )
            return
        raise ValueError(
            f"URL hostname '{hostname}' could not be resolved, so it cannot be "
            "checked against blocked IP ranges. Set "
            "ASSERT_ALLOW_UNRESOLVABLE_ENDPOINTS=1 to allow unresolvable hosts."
        ) from e

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
    r"access[_-]?token|refresh[_-]?token|private[_-]?key|client[_-]?secret|"
    r"azure[_-]?ad[_-]?token)",
    re.IGNORECASE,
)

_REDACTED = "[REDACTED]"

# Free-text redaction. sanitize_payload() keys off dict keys, so it cannot see a
# secret embedded in a string — a traceback or a span attribute value carries its
# secrets inline. These patterns are deliberately narrow: over-redaction silently
# corrupts evaluation data, which is a different kind of harm, so only
# high-confidence credential shapes are matched.

_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?P<key>api[_-]?key|auth[_-]?token|secret|password|passwd|credential|"
    r"authorization|access[_-]?token|refresh[_-]?token|private[_-]?key|"
    r"client[_-]?secret|azure[_-]?ad[_-]?token)"
    r"(?P<sep>[\"']?\s*[:=]\s*[\"']?)"
    r"(?P<value>[^\s\"',;)}\]]+)",
    re.IGNORECASE,
)

_AUTH_SCHEME_RE = re.compile(
    r"\b(?P<scheme>Bearer|Basic)\s+(?P<token>[A-Za-z0-9._\-+/=]+)",
    re.IGNORECASE,
)

_URL_CREDENTIALS_RE = re.compile(
    r"(?P<prefix>[a-z][a-z0-9+.\-]*://[^:/@\s]+:)(?P<password>[^@/\s]+)@",
    re.IGNORECASE,
)

_TOKEN_SHAPE_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_\-]{16,}"          # OpenAI-style keys
    r"|AKIA[0-9A-Z]{16}"                    # AWS access key IDs
    r"|gh[pousr]_[A-Za-z0-9]{16,}"          # GitHub tokens
    r"|eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)"  # JWTs
)


def redact_text(text: str) -> str:
    """Redact credentials embedded in free text.

    Complements :func:`sanitize_payload`, which can only redact whole values
    whose *key* looks sensitive. Use this on diagnostic and telemetry strings —
    tracebacks, span attribute values — where the secret is inline.

    This is best-effort pattern matching, not a guarantee. It will not catch a
    credential with no recognisable prefix or surrounding key name.
    """
    if not text:
        return text
    out = _URL_CREDENTIALS_RE.sub(
        lambda m: f"{m.group('prefix')}{_REDACTED}@", str(text)
    )
    # Auth schemes are matched before key/value assignments. In
    # "Authorization: Bearer <token>" the assignment pattern would otherwise
    # capture the literal word "Bearer" as the value and leave the token intact.
    out = _AUTH_SCHEME_RE.sub(lambda m: f"{m.group('scheme')} {_REDACTED}", out)
    out = _SECRET_ASSIGNMENT_RE.sub(
        lambda m: f"{m.group('key')}{m.group('sep')}{_REDACTED}", out
    )
    return _TOKEN_SHAPE_RE.sub(_REDACTED, out)


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

