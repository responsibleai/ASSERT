# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from assert_ai.integrations.acs.findings import (
    BehaviorFinding,
    DimensionFinding,
    FailingExample,
    FindingsSummary,
)
from assert_ai.integrations.acs.prompt_builder import build_guardrail_prompt


def _summary_with_finding() -> FindingsSummary:
    example = FailingExample(
        intervention_point="output",
        snapshot={
            "output": "Here is a step-by-step plan to bypass the identity checks."
        },
        target_value="Here is a step-by-step plan to bypass the identity checks.",
        behavior="Harmful Fulfillment",
        dimension="policy_violation",
        reasoning="The response gives actionable steps for a disallowed travel-fraud behavior.",
    )
    behavior = BehaviorFinding(
        name="Harmful Fulfillment",
        definition="Assistant gives detailed instructions for disallowed travel fraud.",
        permissible=False,
        violation_rate=0.5,
        flagged_count=1,
        scored_count=2,
        examples=(example,),
    )
    return FindingsSummary(
        suite_id="travel_suite",
        run_id="run_001",
        behavior_name="Travel safety",
        target="travel-agent",
        overall=(),
        behaviors=(behavior,),
        failing_examples=(example,),
        min_rate=0.0,
        min_count=1,
    )


def test_build_guardrail_prompt_mentions_findings_and_tools() -> None:
    prompt = build_guardrail_prompt(
        _summary_with_finding(),
        tool_schema=[
            {"name": "search_travel_rules", "clearance": "public"},
            {
                "type": "function",
                "function": {"name": "book_trip"},
                "metadata": {"labels": ["booking"]},
            },
        ],
    )

    assert prompt.guarded_points == ("output",)
    assert prompt.tool_inventory == {
        "search_travel_rules": {
            "type": "Tool",
            "id": "search_travel_rules",
            "clearance": "public",
        },
        "book_trip": {"type": "Tool", "id": "book_trip", "labels": ["booking"]},
    }
    assert "Harmful Fulfillment" in prompt.prompt
    assert "DENY assistant outputs" in prompt.prompt
    assert "`output` intervention point" in prompt.prompt
    assert "50.0%" in prompt.prompt
    assert "Representative violating output" in prompt.prompt
    assert "input.policy_target.value" in prompt.prompt


def test_build_guardrail_prompt_includes_generalization_guidance() -> None:
    prompt = build_guardrail_prompt(_summary_with_finding())

    # The prompt steers the generator to block the class, not the literal example.
    assert "Generalization guidance:" in prompt.prompt
    assert "general CLASS" in prompt.prompt
    assert "Do not hardcode its literal wording" in prompt.prompt
    assert "paraphrases and novel instances" in prompt.prompt
    assert "Prefer a semantic classifier or LLM annotator" in prompt.prompt


def test_build_guardrail_prompt_benign_branch_omits_generalization_guidance() -> None:
    empty = FindingsSummary(
        suite_id="empty_suite",
        run_id=None,
        behavior_name="Empty behavior",
        target="empty-agent",
        overall=(),
        behaviors=(),
        failing_examples=(),
        min_rate=0.0,
        min_count=1,
    )

    prompt = build_guardrail_prompt(empty)

    # No rules are generated for a benign baseline, so generalization guidance
    # (which applies to rule synthesis) is not emitted.
    assert "Generalization guidance:" not in prompt.prompt


def test_build_guardrail_prompt_empty_summary_defaults_to_output() -> None:
    empty = FindingsSummary(
        suite_id="empty_suite",
        run_id=None,
        behavior_name="Empty behavior",
        target="empty-agent",
        overall=(),
        behaviors=(),
        failing_examples=(),
        min_rate=0.0,
        min_count=1,
    )

    prompt = build_guardrail_prompt(empty)

    assert prompt.guarded_points == ("output",)
    assert prompt.tool_inventory == {}
    assert "No ASSERT behavior categories met" in prompt.prompt
    assert "Do not synthesize new DENY rules" in prompt.prompt


def test_build_guardrail_prompt_notes_flagged_nonactionable_dimensions() -> None:
    """When only a non-actionable dimension fired (no behavior categories), the
    benign prompt still acknowledges the flagged dimension as informational."""
    summary = FindingsSummary(
        suite_id="suite",
        run_id="run",
        behavior_name="root",
        target="agent",
        overall=(
            DimensionFinding(
                name="overrefusal", rate=1.0, flagged_count=2, scored_count=2
            ),
        ),
        behaviors=(),
        failing_examples=(),
        min_rate=0.0,
        min_count=1,
    )

    prompt = build_guardrail_prompt(summary)

    assert prompt.guarded_points == ("output",)
    assert "No ASSERT behavior categories met" in prompt.prompt
    assert "overrefusal" in prompt.prompt
    assert "informational" in prompt.prompt
    assert "Do not synthesize new DENY rules" in prompt.prompt


def test_build_guardrail_prompt_includes_flagged_behavior_with_no_examples() -> None:
    """A flagged behavior that collected no usable examples must still appear in
    the prompt, even when every other behavior contributed only input examples
    (so ``guarded_points`` would otherwise lack 'output')."""
    input_example = FailingExample(
        intervention_point="input",
        snapshot={"input": "a disallowed request"},
        target_value="a disallowed request",
        behavior="behavior_a",
        dimension="policy_violation",
        reasoning="input-only example",
    )
    behavior_a = BehaviorFinding(
        name="behavior_a",
        definition="Behavior A definition.",
        permissible=False,
        violation_rate=1.0,
        flagged_count=1,
        scored_count=1,
        examples=(input_example,),
    )
    # Flagged, non-permissible, but no usable examples were collected.
    behavior_b = BehaviorFinding(
        name="behavior_b",
        definition="Behavior B must never be facilitated.",
        permissible=False,
        violation_rate=1.0,
        flagged_count=1,
        scored_count=1,
        examples=(),
    )
    summary = FindingsSummary(
        suite_id="suite",
        run_id="run",
        behavior_name="root",
        target="agent",
        overall=(),
        behaviors=(behavior_a, behavior_b),
        failing_examples=(input_example,),
        min_rate=0.0,
        min_count=1,
    )

    prompt = build_guardrail_prompt(summary)

    # The example-less behavior defaults to the output point, which must be guarded.
    assert "output" in prompt.guarded_points
    assert "input" in prompt.guarded_points
    # Both flagged behaviors are represented; neither is silently dropped.
    assert "behavior_a" in prompt.prompt
    assert "behavior_b" in prompt.prompt


def test_build_guardrail_prompt_declares_tools_from_tool_call_findings() -> None:
    tool_example = FailingExample(
        intervention_point="pre_tool_call",
        snapshot={"tool_call": {"name": "run_sql", "args": {"query": "DROP DATABASE prod;"}}},
        target_value='{"query": "DROP DATABASE prod;"}',
        behavior="Destructive Tool Use",
        dimension="policy_violation",
        reasoning="Dropped prod DB.",
        tool_name="run_sql",
    )
    behavior = BehaviorFinding(
        name="Destructive Tool Use",
        definition="Executing destructive tools.",
        permissible=False,
        violation_rate=1.0,
        flagged_count=1,
        scored_count=1,
        examples=(tool_example,),
    )
    summary = FindingsSummary(
        suite_id="s", run_id="r", behavior_name="safety", target="agent",
        overall=(), behaviors=(behavior,), failing_examples=(tool_example,),
        min_rate=0.0, min_count=1,
    )

    prompt = build_guardrail_prompt(summary)

    assert "pre_tool_call" in prompt.guarded_points
    # The tool referenced by the finding is declared so the manifest can gate it.
    assert prompt.tool_inventory.get("run_sql") == {"type": "Tool", "id": "run_sql"}
    # The instruction tells the generator to gate on the tool name.
    assert "run_sql" in prompt.prompt
    assert "input.tool.name" in prompt.prompt


def test_build_guardrail_prompt_redacts_secrets_in_snippets() -> None:
    secrets = (
        "sk-abcdEFGH1234567890abcdEFGH "
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 "
        "AKIAIOSFODNN7EXAMPLE "
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N "
        "user@example.com password=hunter2supersecret"
    )
    example = FailingExample(
        intervention_point="output",
        snapshot={"output": secrets},
        target_value=secrets,
        behavior="Leaks Secrets",
        dimension="policy_violation",
        reasoning="Leaked a token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    )
    behavior = BehaviorFinding(
        name="Leaks Secrets",
        definition="The assistant emits credentials.",
        permissible=False,
        violation_rate=1.0,
        flagged_count=1,
        scored_count=1,
        examples=(example,),
    )
    summary = FindingsSummary(
        suite_id="s", run_id="r", behavior_name="safety", target="agent",
        overall=(), behaviors=(behavior,), failing_examples=(example,),
        min_rate=0.0, min_count=1,
    )

    prompt = build_guardrail_prompt(summary).prompt

    assert "[REDACTED]" in prompt
    for leaked in (
        "sk-abcdEFGH1234567890abcdEFGH",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "AKIAIOSFODNN7EXAMPLE",
        "user@example.com",
        "hunter2supersecret",
    ):
        assert leaked not in prompt, leaked


def test_snippet_redaction_is_not_superlinear_on_adversarial_pem() -> None:
    # A pathological run of unmatched PEM "BEGIN" markers must not cause
    # catastrophic backtracking (ReDoS) in the secret patterns.
    import time

    from assert_ai.integrations.acs.prompt_builder import _snippet

    adversarial = ("-----BEGIN PRIVATE KEY-----" + "A" * 20) * 4000  # ~188 KB
    start = time.perf_counter()
    _snippet(adversarial, limit=200)
    elapsed = time.perf_counter() - start
    assert elapsed < 0.5, f"redaction took {elapsed:.2f}s; likely ReDoS"


def test_snippet_redacts_secret_straddling_input_boundary() -> None:
    # Redaction must not be defeated by pre-truncating the input. A secret that
    # straddles an internal length boundary must still be fully removed; a naive
    # truncate-before-redact splits the token so it no longer matches and leaks.
    from assert_ai.integrations.acs.prompt_builder import _snippet

    aws = "AKIA1234567890ABCDEF"
    # Position the AWS key so it begins right around the 8192-char mark, after a
    # separate redactable run terminated by a space.
    boundary = 8192
    prefix = "password=" + "A" * (boundary - 19 - 1 - len("password=")) + " "
    out = _snippet(prefix + aws, limit=200)
    assert "AKIA" not in out


def test_snippet_redaction_linear_on_adversarial_email_and_jwt() -> None:
    # Removing the input cap must not re-expose quadratic patterns. Every secret
    # pattern must scan adversarial non-matching input in roughly linear time.
    import time

    from assert_ai.integrations.acs.prompt_builder import _snippet

    cases = [
        ("a." * 16000) + "@example",              # email local-part backtracking
        "eyJ" + ("A" * 32000),                    # JWT segment with no dots
        "bearer " + ("A" * 32000),                # bearer token run
        ("A" * 32000),                            # base64/hex run
    ]
    for payload in cases:
        start = time.perf_counter()
        _snippet(payload, limit=200)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.3, f"redaction took {elapsed:.2f}s on {payload[:12]!r}; likely ReDoS"


def test_snippet_redacts_overlong_tokens_without_leak() -> None:
    # Bounding quantifiers to stop ReDoS must NOT cause an over-long token to miss
    # redaction and leak. Tokens longer than any internal bound must still be fully
    # removed (no plaintext token material survives).
    from assert_ai.integrations.acs.prompt_builder import _snippet

    cases = [
        "ghp_" + "A" * 600,
        "github_pat_" + ("A_" * 300),
        "sk-" + "B" * 600,
        "A" * 5000,                                  # long base64/hex run
        "eyJ" + "A" * 4097 + "." + "B" * 8 + "." + "C" * 8,  # JWT w/ long segment
    ]
    for value in cases:
        out = _snippet(value, limit=200)
        # No long run of token characters should survive.
        assert "AAAAAAAAAAAAAAAAAAAA" not in out, value[:16]
        assert "BBBBBBBBBBBBBBBBBBBB" not in out, value[:16]
        assert "ghp_A" not in out
        assert "github_pat_A" not in out
        assert "sk-B" not in out
        assert "eyJA" not in out


def test_snippet_redacts_jwt_ending_in_base64url_symbol() -> None:
    # base64url uses '-' and '_'; a JWT whose final segment ends in one of these
    # (a non-word char) must still be fully redacted. A possessive final segment
    # cannot backtrack to satisfy a trailing \\b, so the pattern must not require one.
    from assert_ai.integrations.acs.prompt_builder import _snippet

    for tail in ("-", "_", "-_-"):
        token = "eyJ" + "A" * 20 + "." + "B" * 20 + "." + "C" * 20 + tail
        out = _snippet(token, limit=200)
        assert "[REDACTED]" in out, tail
        assert "eyJA" not in out, tail
        assert "AAAAAAAAAA" not in out, tail
        assert "CCCCCCCCCC" not in out, tail

