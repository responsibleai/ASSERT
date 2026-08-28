# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import base64

import pytest

from assert_ai.integrations.langfuse import (
    LangfuseAuthError,
    LangfuseConfigurationError,
    LangfuseHTTPClient,
    LangfuseHTTPError,
    LangfuseResponseError,
)
from tests.langfuse_fake_server import fake_langfuse_server


def test_client_posts_exact_paths_headers_and_bodies() -> None:
    with fake_langfuse_server() as server:
        client = LangfuseHTTPClient(
            base_url=server.base_url,
            public_key="public-placeholder",
            secret_key="secret-placeholder",
        )
        assert client.post_trace({"resourceSpans": []}) == {"ok": True}
        assert client.post_score({"name": "safe", "value": 1.0}) == {"ok": True}

    assert [request.path for request in server.requests] == [
        "/api/public/otel/v1/traces",
        "/api/public/scores",
    ]
    assert [request.method for request in server.requests] == ["POST", "POST"]
    expected_auth = "Basic " + base64.b64encode(
        b"public-placeholder:secret-placeholder"
    ).decode("ascii")
    assert server.requests[0].headers["authorization"] == expected_auth
    assert server.requests[0].headers["content-type"] == "application/json"
    assert server.requests[0].headers["x-langfuse-ingestion-version"] == "4"
    assert "x-langfuse-ingestion-version" not in server.requests[1].headers
    assert server.requests[0].body == {"resourceSpans": []}
    assert server.requests[1].body == {"name": "safe", "value": 1.0}


def test_client_maps_403_to_typed_auth_error_without_response_body() -> None:
    with fake_langfuse_server() as server:
        server.response_status = 403
        server.response_body = b'{"message":"credential details must not leak"}'
        client = LangfuseHTTPClient(
            base_url=server.base_url,
            public_key="public-placeholder",
            secret_key="secret-placeholder",
        )
        with pytest.raises(LangfuseAuthError) as raised:
            client.post_score({"name": "safe", "value": 1.0})

    assert raised.value.status_code == 403
    assert raised.value.endpoint == "/api/public/scores"
    assert "credential details" not in str(raised.value)


def test_client_rejects_invalid_json_response() -> None:
    with fake_langfuse_server() as server:
        server.response_body = b"not-json"
        client = LangfuseHTTPClient(
            base_url=server.base_url,
            public_key="public-placeholder",
            secret_key="secret-placeholder",
        )
        with pytest.raises(LangfuseResponseError, match="invalid JSON"):
            client.post_trace({"resourceSpans": []})


def test_client_rejects_redirect_without_forwarding_credentials() -> None:
    with fake_langfuse_server() as redirect_target:
        with fake_langfuse_server() as server:
            server.response_status = 302
            server.response_headers["Location"] = (
                redirect_target.base_url + "/credential-target"
            )
            client = LangfuseHTTPClient(
                base_url=server.base_url,
                public_key="public-placeholder",
                secret_key="secret-placeholder",
            )
            with pytest.raises(LangfuseHTTPError) as raised:
                client.post_score({"name": "safe", "value": 1.0})

    assert len(server.requests) == 1
    assert redirect_target.requests == []
    assert raised.value.status_code == 302


def test_client_from_env_requires_current_documented_names() -> None:
    with pytest.raises(LangfuseConfigurationError) as raised:
        LangfuseHTTPClient.from_env({})
    assert "LANGFUSE_BASE_URL" in str(raised.value)
    assert "LANGFUSE_PUBLIC_KEY" in str(raised.value)
    assert "LANGFUSE_SECRET_KEY" in str(raised.value)

    with pytest.raises(LangfuseConfigurationError, match="must use HTTPS"):
        LangfuseHTTPClient(
            base_url="http://example.test",
            public_key="public-placeholder",
            secret_key="secret-placeholder",
        )

    with pytest.raises(LangfuseConfigurationError, match="without credentials"):
        LangfuseHTTPClient(
            base_url="https://user:password@example.test/path",
            public_key="public-placeholder",
            secret_key="secret-placeholder",
        )
