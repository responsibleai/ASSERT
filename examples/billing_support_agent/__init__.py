# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Reference callable billing-support agent.

:mod:`examples.billing_support_agent.agent` exposes ``chat_baseline``, the
callable ASSERT evaluates. The identity-verification gate lives only in the
system prompt, so the agent can be pressured into performing a high-risk action
on an unverified session — the failure the eval suites measure.
"""
