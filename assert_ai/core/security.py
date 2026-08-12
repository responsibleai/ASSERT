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


def validate_callable_ref(callable_ref: str, *, config_path: Path | None = None) -> None:
    """Validate a callable reference before dynamic import.

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

_NAT64_WELL_KNOWN_PREFIX = ipaddress.ip_network("64:ff9b::/96")
_IPV4_COMPATIBLE_PREFIX = ipaddress.ip_network("::/96")
_IPV4_COMPATIBLE_RESERVED = {
    ipaddress.ip_address("::"),
    ipaddress.ip_address("::1"),
}

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

    # Validate IP literals directly; hostnames are checked after DNS resolution.
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        # Not an IP literal — resolve hostname and check resulting IPs.
        if hostname.lower() not in _LOCAL_DEV_HOSTNAMES:
            _validate_resolved_ips(hostname)
    else:
        validate_resolved_endpoint_ip(hostname, hostname)


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

    for _family, _type, _proto, _canonname, sockaddr in addrinfo:
        validate_resolved_endpoint_ip(hostname, sockaddr[0])


def _canonicalize_endpoint_ip(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Return the address an IPv4-in-IPv6 endpoint ultimately reaches."""
    if not isinstance(ip, ipaddress.IPv6Address):
        return ip

    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped

    if ip in _NAT64_WELL_KNOWN_PREFIX or (
        ip in _IPV4_COMPATIBLE_PREFIX and ip not in _IPV4_COMPATIBLE_RESERVED
    ):
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)

    return ip


def validate_resolved_endpoint_ip(hostname: str, ip_str: str) -> None:
    """Validate one DNS answer immediately before an endpoint connection.

    ``validate_endpoint_url`` performs an eager DNS check for fast feedback, but
    HTTP clients resolve again when they connect.  Call this helper on the
    resolver results that will actually be used for the socket so a hostname
    cannot pass validation with a public address and later rebind to a private
    one.
    """
    if os.environ.get("ASSERT_ALLOW_PRIVATE_ENDPOINTS", "").lower() in (
        "1",
        "true",
        "yes",
    ):
        return
    if hostname.lower() in _LOCAL_DEV_HOSTNAMES:
        return

    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError as exc:
        raise ValueError(
            f"Resolver returned a non-IP address for endpoint hostname '{hostname}': {ip_str}"
        ) from exc

    # Mapped, NAT64, and deprecated IPv4-compatible IPv6 addresses inherit the
    # security properties of the IPv4 endpoint they ultimately reach.
    checked_ip = _canonicalize_endpoint_ip(ip)

    for network in _BLOCKED_IP_RANGES:
        if checked_ip.version == network.version and checked_ip in network:
            log.warning(
                "SSRF protection: hostname '%s' resolves to blocked IP %s (range %s)",
                hostname,
                ip_str,
                network,
            )
            raise ValueError(
                f"URL hostname '{hostname}' resolves to blocked IP range ({network}): {ip_str}"
            )

    # Block unspecified, reserved, documentation, shared, and other special-use
    # addresses as well as multicast.  SSRF targets must resolve to a publicly
    # routable unicast address unless the explicit development override is set.
    if not checked_ip.is_global or checked_ip.is_multicast:
        log.warning(
            "SSRF protection: hostname '%s' resolves to non-public IP %s",
            hostname,
            ip_str,
        )
        raise ValueError(
            f"URL hostname '{hostname}' resolves to a non-public IP address: {ip_str}"
        )


# ── Credential sanitization ────────────────────────────────────

_SENSITIVE_KEYS = re.compile(
    r"(api[_-]?key|auth[_-]?token|secret|password|credential|bearer|authorization|"
    r"access[_-]?token|refresh[_-]?token|private[_-]?key|client[_-]?secret|"
    r"azure[_-]?ad[_-]?token)",
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
