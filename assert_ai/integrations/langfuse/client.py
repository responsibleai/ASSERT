# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Minimal standard-library client for the Langfuse public HTTP APIs."""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from assert_ai.integrations.langfuse.errors import (
    LangfuseAuthError,
    LangfuseConfigurationError,
    LangfuseConnectionError,
    LangfuseHTTPError,
    LangfuseResponseError,
)

_OTLP_TRACES_PATH = "/api/public/otel/v1/traces"
_SCORES_PATH = "/api/public/scores"


class _RejectRedirectHandler(HTTPRedirectHandler):
    """Keep Basic Auth credentials on the configured Langfuse origin."""

    def redirect_request(
        self,
        request: Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        return None


class LangfuseHTTPClient:
    """Post OTLP traces and scores without importing the Langfuse SDK."""

    def __init__(
        self,
        *,
        base_url: str,
        public_key: str,
        secret_key: str,
        timeout_s: float = 30.0,
    ) -> None:
        self._base_url = _validate_base_url(base_url)
        if not public_key or not secret_key:
            raise LangfuseConfigurationError(
                "Langfuse public and secret keys must both be non-empty"
            )
        if timeout_s <= 0:
            raise LangfuseConfigurationError("timeout_s must be greater than zero")
        credentials = f"{public_key}:{secret_key}".encode()
        self._authorization = "Basic " + base64.b64encode(credentials).decode("ascii")
        self._timeout_s = timeout_s

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        timeout_s: float = 30.0,
    ) -> "LangfuseHTTPClient":
        """Build a client from current documented Langfuse environment names."""
        values = os.environ if env is None else env
        required = (
            "LANGFUSE_BASE_URL",
            "LANGFUSE_PUBLIC_KEY",
            "LANGFUSE_SECRET_KEY",
        )
        missing = [name for name in required if not values.get(name)]
        if missing:
            raise LangfuseConfigurationError(
                "Missing required Langfuse environment variable(s): "
                + ", ".join(missing)
            )
        return cls(
            base_url=values["LANGFUSE_BASE_URL"],
            public_key=values["LANGFUSE_PUBLIC_KEY"],
            secret_key=values["LANGFUSE_SECRET_KEY"],
            timeout_s=timeout_s,
        )

    def post_trace(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Post one OTLP/HTTP JSON trace payload."""
        return self._post_json(
            _OTLP_TRACES_PATH,
            payload,
            extra_headers={"x-langfuse-ingestion-version": "4"},
        )

    def post_score(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Post one score through the stable public Scores API."""
        return self._post_json(_SCORES_PATH, payload)

    def _post_json(
        self,
        endpoint: str,
        payload: dict[str, Any],
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "Authorization": self._authorization,
            "Content-Type": "application/json",
            "User-Agent": "assert-ai-langfuse-bridge",
        }
        headers.update(extra_headers or {})
        request = Request(
            self._base_url + endpoint,
            data=json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            opener = build_opener(_RejectRedirectHandler())
            with opener.open(request, timeout=self._timeout_s) as response:
                raw = response.read()
        except HTTPError as exc:
            error_type = (
                LangfuseAuthError
                if exc.code in (401, 403)
                else LangfuseHTTPError
            )
            raise error_type(status_code=exc.code, endpoint=endpoint) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise LangfuseConnectionError(
                f"Unable to reach the Langfuse endpoint for {endpoint}"
            ) from exc

        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LangfuseResponseError(
                f"Langfuse returned invalid JSON for {endpoint}"
            ) from exc
        if not isinstance(decoded, dict):
            raise LangfuseResponseError(
                f"Langfuse returned a non-object JSON response for {endpoint}"
            )
        return decoded


def _validate_base_url(value: str) -> str:
    base_url = value.strip().rstrip("/")
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise LangfuseConfigurationError(
            "LANGFUSE_BASE_URL must be an http(s) origin without credentials or a path"
        )
    return base_url


__all__ = ["LangfuseHTTPClient"]
