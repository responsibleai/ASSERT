# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Sandboxed action mediation: run a configured agent safely and judge its actions.

Agent evaluation is moving from scoring text to scoring *actions*. A configured
agent does not just answer — it reads customer records, applies credits, restores
service, sends messages, and calls partner APIs. An adversarial eval has to test
those actions, but its whole point is to try to make the agent do something
harmful, which is exactly what cannot be allowed to reach a real backend.

This subpackage sits at the agent's tool-call boundary and decides, per call,
one of three enforcement modes, recording every attempt as judge-visible
evidence:

    pass   -> execute the real (sandbox-bound) tool; the agent sees the truth
    mock   -> suppress the real tool; the agent sees a synthetic result
    block  -> suppress the real tool; the agent sees an explicit denial

Two files, two questions
------------------------
The enforcement decision and the mock content are deliberately separate:

    policy.yaml  -> WHETHER a call is passed, mocked, or blocked   (safety)
    mocks.yaml   -> WHAT a mocked call returns                     (fidelity)

That split keeps one base policy reusable per agent while mock content varies
per behavior under test, and it carries a safety property worth stating plainly:
**the mock file can never change an enforcement decision.** It only supplies
content for a call the policy already decided to mock, so adding mock fidelity
can never weaken containment.

Relationship to other layers
----------------------------
`Agent Hooks <https://github.com/responsibleai/agent-hooks>`_ provides the shared
pre/post tool-call seam. ACS / Agent Shield are the *production* consumers of
that seam, enforcing policy on live traffic. This is the *eval-time* consumer of
the same seam: it adds result substitution so an eval can continue past a risky
call without causing the real side effect. ASSERT then judges the resulting
evidence.

Stock containment
-----------------
ASSERT ships a Docker baseline for configured container targets: read-only
filesystem, dropped capabilities, deny-by-default network egress with an audit
proxy, host-side credential routing, and per-case startup/teardown. Endpoint
targets remain available when the user already owns that boundary.
"""

from __future__ import annotations

from assert_ai.integrations.sandbox.agent_hooks_context import AgentHooksContextBuilder
from assert_ai.integrations.sandbox.mediation_setup import (
    MediationSetup,
    SetupError,
    TargetSpec,
    load_setup,
    validate_setup,
)
from assert_ai.integrations.sandbox.mediator import ActionMediator
from assert_ai.integrations.sandbox.mocks import MockCall, MockLibrary
from assert_ai.integrations.sandbox.policy import MediationPolicy
from assert_ai.integrations.sandbox.records import MediationDecision, MediationRecord
from assert_ai.integrations.sandbox.runtime import ContainerSpec, SandboxRuntimeError
from assert_ai.integrations.sandbox.session import SandboxedEndpointSession
from assert_ai.integrations.sandbox.tool_host import AgentHooksToolHost

__all__ = [
    "ActionMediator",
    "AgentHooksContextBuilder",
    "AgentHooksToolHost",
    "ContainerSpec",
    "MediationDecision",
    "MediationPolicy",
    "MediationRecord",
    "MediationSetup",
    "MockCall",
    "MockLibrary",
    "SetupError",
    "SandboxRuntimeError",
    "SandboxedEndpointSession",
    "TargetSpec",
    "load_setup",
    "validate_setup",
]
