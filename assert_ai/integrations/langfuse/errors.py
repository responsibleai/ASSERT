# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Typed errors raised by the optional Langfuse artifact exporter."""


class LangfuseAdapterError(Exception):
    """Base class for Langfuse integration failures."""


class LangfuseConfigurationError(LangfuseAdapterError):
    """The local Langfuse configuration is missing or invalid."""


class LangfuseContractError(LangfuseAdapterError):
    """An ASSERT artifact does not satisfy the export contract."""


class LangfuseConnectionError(LangfuseAdapterError):
    """The Langfuse endpoint could not be reached."""


class LangfuseHTTPError(LangfuseAdapterError):
    """Langfuse returned an unsuccessful HTTP response."""

    def __init__(self, *, status_code: int, endpoint: str) -> None:
        self.status_code = status_code
        self.endpoint = endpoint
        super().__init__(f"Langfuse returned HTTP {status_code} for {endpoint}")


class LangfuseAuthError(LangfuseHTTPError):
    """Langfuse rejected the configured project credentials."""


class LangfuseResponseError(LangfuseAdapterError):
    """Langfuse returned a response that did not match its public API."""
