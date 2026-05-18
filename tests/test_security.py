"""Tests for p2m.core.security — input validation, SSRF prevention, and credential sanitization."""

from __future__ import annotations

import json
import socket
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from p2m.core.security import (
    sanitize_payload,
    validate_callable_ref,
    validate_endpoint_url,
    validate_module_ref,
    validate_sys_path_addition,
)
from p2m.core.session import _sanitize_response_text


# ── validate_callable_ref ──────────────────────────────────────


class ValidateCallableRefTest(unittest.TestCase):
    def test_valid_ref_passes(self) -> None:
        validate_callable_ref("my_module:my_func")

    def test_dotted_module_path_passes(self) -> None:
        validate_callable_ref("my.package.module:run_agent")

    def test_missing_colon_raises(self) -> None:
        with self.assertRaises(ValueError, msg="must be in"):
            validate_callable_ref("my_module_no_func")

    def test_empty_string_raises(self) -> None:
        with self.assertRaises(ValueError):
            validate_callable_ref("")

    def test_empty_module_part_raises(self) -> None:
        with self.assertRaises(ValueError, msg="both module path and function name"):
            validate_callable_ref(":my_func")

    def test_empty_func_part_raises(self) -> None:
        with self.assertRaises(ValueError, msg="both module path and function name"):
            validate_callable_ref("my_module:")

    def test_pycache_in_module_path_raises(self) -> None:
        with self.assertRaises(ValueError, msg="disallowed"):
            validate_callable_ref("__pycache__.module:func")

    def test_git_in_module_path_raises(self) -> None:
        with self.assertRaises(ValueError, msg="disallowed"):
            validate_callable_ref("repo..git.hooks:func")

    def test_site_packages_in_module_path_raises(self) -> None:
        with self.assertRaises(ValueError, msg="disallowed"):
            validate_callable_ref("site-packages.evil:func")

    def test_node_modules_in_module_path_raises(self) -> None:
        with self.assertRaises(ValueError, msg="disallowed"):
            validate_callable_ref("node_modules.pkg:func")


# ── validate_module_ref ────────────────────────────────────────


class ValidateModuleRefTest(unittest.TestCase):
    def test_valid_module_passes(self) -> None:
        validate_module_ref("my_tool_module")

    def test_dotted_module_passes(self) -> None:
        validate_module_ref("my.tools.backend")

    def test_empty_raises(self) -> None:
        with self.assertRaises(ValueError):
            validate_module_ref("")

    def test_pycache_raises(self) -> None:
        with self.assertRaises(ValueError, msg="disallowed"):
            validate_module_ref("__pycache__.module")


# ── validate_sys_path_addition ─────────────────────────────────


class ValidateSysPathAdditionTest(unittest.TestCase):
    def test_cwd_child_passes(self) -> None:
        with TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            child = cwd / "subdir"
            child.mkdir()
            with patch("p2m.core.security.Path.cwd", return_value=cwd):
                validate_sys_path_addition(child)

    def test_config_dir_child_passes(self) -> None:
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "configs"
            config_dir.mkdir()
            tool_dir = config_dir / "tools"
            tool_dir.mkdir()
            config_path = config_dir / "eval.yaml"
            # cwd is elsewhere
            with patch("p2m.core.security.Path.cwd", return_value=Path("/tmp/other")):
                validate_sys_path_addition(tool_dir, config_path=config_path)

    def test_system_path_raises(self) -> None:
        with patch("p2m.core.security.Path.cwd", return_value=Path("/tmp/workspace")):
            with self.assertRaises(ValueError, msg="Refusing"):
                validate_sys_path_addition(Path("/usr/lib/python3/dist-packages"))


# ── validate_endpoint_url ──────────────────────────────────────


class ValidateEndpointUrlTest(unittest.TestCase):
    """SSRF prevention tests."""

    # ── Blocked IPs ──

    def test_loopback_ip_blocked(self) -> None:
        with self.assertRaises(ValueError, msg="blocked"):
            validate_endpoint_url("http://127.0.0.1/api")

    def test_private_10_blocked(self) -> None:
        with self.assertRaises(ValueError, msg="blocked"):
            validate_endpoint_url("http://10.0.0.1/api")

    def test_private_172_blocked(self) -> None:
        with self.assertRaises(ValueError, msg="blocked"):
            validate_endpoint_url("http://172.16.0.1/api")

    def test_private_192_blocked(self) -> None:
        with self.assertRaises(ValueError, msg="blocked"):
            validate_endpoint_url("http://192.168.1.1/api")

    def test_link_local_metadata_ip_blocked(self) -> None:
        with self.assertRaises(ValueError, msg="blocked"):
            validate_endpoint_url("http://169.254.169.254/latest/meta-data")

    def test_azure_wireserver_ip_blocked(self) -> None:
        with self.assertRaises(ValueError, msg="blocked"):
            validate_endpoint_url("http://168.63.129.16/metadata")

    # ── Blocked hostnames ──

    def test_gcp_metadata_hostname_blocked(self) -> None:
        with self.assertRaises(ValueError, msg="blocked"):
            validate_endpoint_url("http://metadata.google.internal/computeMetadata/v1/")

    def test_metadata_shortname_blocked(self) -> None:
        with self.assertRaises(ValueError, msg="blocked"):
            validate_endpoint_url("http://metadata/")

    # ── Scheme validation ──

    def test_ftp_scheme_blocked(self) -> None:
        with self.assertRaises(ValueError, msg="scheme"):
            validate_endpoint_url("ftp://example.com/file")

    def test_file_scheme_blocked(self) -> None:
        with self.assertRaises(ValueError, msg="scheme"):
            validate_endpoint_url("file:///etc/passwd")

    # ── DNS rebinding ──

    def test_hostname_resolving_to_metadata_ip_blocked(self) -> None:
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0)),
        ]
        with patch("p2m.core.security.socket.getaddrinfo", return_value=fake_addrinfo):
            with self.assertRaises(ValueError, msg="blocked IP range"):
                validate_endpoint_url("http://evil-rebind.attacker.com/api")

    def test_hostname_resolving_to_private_ip_blocked(self) -> None:
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0)),
        ]
        with patch("p2m.core.security.socket.getaddrinfo", return_value=fake_addrinfo):
            with self.assertRaises(ValueError, msg="blocked IP range"):
                validate_endpoint_url("http://evil-rebind.attacker.com/api")

    def test_dns_failure_allows_passthrough(self) -> None:
        with patch(
            "p2m.core.security.socket.getaddrinfo",
            side_effect=socket.gaierror("Name resolution failed"),
        ):
            # Should not raise — will fail at connection time
            validate_endpoint_url("http://nonexistent.example.com/api")

    # ── Allowed URLs ──

    def test_public_ip_allowed(self) -> None:
        validate_endpoint_url("http://93.184.216.34/api")

    def test_public_hostname_with_public_ip_allowed(self) -> None:
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        ]
        with patch("p2m.core.security.socket.getaddrinfo", return_value=fake_addrinfo):
            validate_endpoint_url("https://api.example.com/v1/chat")

    def test_localhost_skips_dns_resolution(self) -> None:
        """localhost is in _LOCAL_DEV_HOSTNAMES — blocked by IP range but not by DNS check."""
        # localhost resolves to 127.0.0.1 which IS in blocked ranges, but the
        # hostname literal "localhost" is checked as an IP parse first (fails),
        # then skipped from DNS resolution. However 'localhost' is NOT in
        # _BLOCKED_HOSTNAMES, so it passes the hostname check.
        # The IP literal check only fires for actual IP addresses.
        # Net: localhost passes because it's in _LOCAL_DEV_HOSTNAMES.
        with patch("p2m.core.security.socket.getaddrinfo") as mock_dns:
            validate_endpoint_url("http://localhost:8080/api")
            mock_dns.assert_not_called()

    def test_allow_private_flag_skips_all_checks(self) -> None:
        validate_endpoint_url("http://127.0.0.1/api", allow_private=True)

    def test_allow_private_env_var_skips_all_checks(self) -> None:
        with patch.dict("os.environ", {"P2M_ALLOW_PRIVATE_ENDPOINTS": "1"}):
            validate_endpoint_url("http://10.0.0.1/api")


# ── sanitize_payload ───────────────────────────────────────────


class SanitizePayloadTest(unittest.TestCase):
    def test_redacts_api_key(self) -> None:
        result = sanitize_payload({"api_key": "sk-secret123", "model": "gpt-4"})
        self.assertEqual(result["api_key"], "[REDACTED]")
        self.assertEqual(result["model"], "gpt-4")

    def test_redacts_auth_token(self) -> None:
        result = sanitize_payload({"auth_token": "tok-abc"})
        self.assertEqual(result["auth_token"], "[REDACTED]")

    def test_redacts_nested_secret(self) -> None:
        result = sanitize_payload({"config": {"password": "hunter2", "host": "db.local"}})
        self.assertEqual(result["config"]["password"], "[REDACTED]")
        self.assertEqual(result["config"]["host"], "db.local")

    def test_redacts_bearer_string_value(self) -> None:
        result = sanitize_payload({"header": "Bearer eyJhbGciOiJSUzI1NiJ9.token"})
        self.assertEqual(result["header"], "[REDACTED]")

    def test_redacts_basic_string_value(self) -> None:
        result = sanitize_payload({"auth": "Basic dXNlcjpwYXNz"})
        self.assertEqual(result["auth"], "[REDACTED]")

    def test_leaves_normal_values(self) -> None:
        payload = {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}
        result = sanitize_payload(payload)
        self.assertEqual(result, payload)

    def test_handles_list_with_sensitive_dicts(self) -> None:
        result = sanitize_payload([{"api_key": "secret"}, {"name": "test"}])
        self.assertEqual(result[0]["api_key"], "[REDACTED]")
        self.assertEqual(result[1]["name"], "test")

    def test_max_depth_redacts_instead_of_passthrough(self) -> None:
        """Payloads exceeding max_depth must be redacted, not returned as-is."""
        # Build a payload nested 12 levels deep with a secret at the bottom
        payload = {"api_key": "sk-secret123"}
        for i in range(12):
            payload = {f"level_{i}": payload}
        result = sanitize_payload(payload)
        serialized = json.dumps(result)
        self.assertNotIn("sk-secret123", serialized)
        self.assertIn("REDACTED", serialized)

    def test_shallow_payload_unaffected_by_max_depth(self) -> None:
        payload = {"api_key": "secret", "model": "gpt-4"}
        result = sanitize_payload(payload)
        self.assertEqual(result["api_key"], "[REDACTED]")
        self.assertEqual(result["model"], "gpt-4")


# ── _sanitize_response_text ────────────────────────────────────


class SanitizeResponseTextTest(unittest.TestCase):
    def test_redacts_bearer_token(self) -> None:
        text = "Your token is Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.longtoken"
        result = _sanitize_response_text(text)
        self.assertNotIn("Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9", result)
        self.assertIn("[REDACTED]", result)

    def test_redacts_basic_auth(self) -> None:
        text = "Authorization: Basic dXNlcjpwYXNzd29yZA=="
        result = _sanitize_response_text(text)
        self.assertNotIn("Basic dXNlcjpwYXNzd29yZA==", result)

    def test_redacts_api_key_pattern(self) -> None:
        text = "Use this key: sk-abc123def456ghi789jkl012mno345pqr"
        result = _sanitize_response_text(text)
        self.assertNotIn("sk-abc123def456ghi789jkl012mno345pqr", result)

    def test_normal_text_unchanged(self) -> None:
        text = "Hello! I can help you plan your trip to Paris."
        self.assertEqual(_sanitize_response_text(text), text)

    def test_empty_string_unchanged(self) -> None:
        self.assertEqual(_sanitize_response_text(""), "")

    def test_short_key_words_not_over_redacted(self) -> None:
        text = "The key insight is that tokens of gratitude are valuable."
        self.assertEqual(_sanitize_response_text(text), text)


if __name__ == "__main__":
    unittest.main()
