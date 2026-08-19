# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Mock setup layer: what a mocked tool call returns.

Split deliberately from the enforcement policy:

    policy.yaml  -> WHETHER a call is passed, mocked, or blocked  (safety)
    mocks.yaml   -> WHAT a mocked call returns                    (fidelity)

See `library.MockLibrary` for the file format and `backends` for the modular
backend seam.
"""
from __future__ import annotations

from .backends import (
    ContractBackend,
    InlineBackend,
    MockBackend,
    MockBackendError,
    MockCall,
    ReplayBackend,
    Resolution,
    ScenarioBackend,
    default_backends,
)
from .library import MockConfigError, MockLibrary, MockRule
from .matching import MatcherError, match_args, match_value

__all__ = [
    "ContractBackend",
    "InlineBackend",
    "MatcherError",
    "MockBackend",
    "MockBackendError",
    "MockCall",
    "MockConfigError",
    "MockLibrary",
    "MockRule",
    "ReplayBackend",
    "Resolution",
    "ScenarioBackend",
    "default_backends",
    "match_args",
    "match_value",
]
