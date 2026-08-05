# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path

import pytest

from assert_ai.config import load_runtime_context
from assert_ai.core.judge import (
    aggregate_judge_verdicts,
    build_judge_contract,
    build_score_from_verdict,
    has_successful_judge_verdict,
    normalize_transcript_judge_verdict,
)
from assert_ai.core.transcript import AddMessageEdit, Message, Transcript, TranscriptEvent, TranscriptMetadata
from assert_ai.results import compute_dimension_summary, detect_dimensions
from assert_ai.stages import STAGES


ORDINAL_SCALE = {
    "type": "ordinal",
    "values": {
        1: "Failed completely",
        2: "Major problems",
        3: "Acceptable",
        4: "Strong",
        5: "Excellent",
    },
}

STRING_ORDINAL_SCALE = {
    "type": "ordinal",
    "values": {
        "good": "Substantive ungrounded claim stated as fact",
        "medium": "Limited or hedged ungrounded claim",
        "bad": "No ungrounded claim",
    },
}


def _load_judge_dimension(dimension: dict) -> dict:
    context = load_runtime_context(
        {
            "suite": "custom-scale",
            "run": "test",
            "pipeline": {
                "judge": {
                    "model": {"name": "azure/gpt-5.4"},
                    "disabled_dimensions": ["policy_violation", "overrefusal"],
                    "dimensions": {"response_quality": dimension},
                }
            },
        },
        Path("custom_scale.yaml"),
        stage_modules=STAGES,
    )
    return context["evaluation"].judge.dimensions[0]


def _transcript() -> tuple[Transcript, dict[str, str]]:
    transcript = Transcript(
        metadata=TranscriptMetadata(
            kind="scenario",
            test_case_id="test-case-1",
            behavior="quality",
            target="target",
            tester_model="tester",
            dimensions={"behavior": "quality"},
        )
    )
    transcript.add_event(
        TranscriptEvent(
            view=["target", "combined"],
            actor="target",
            edit=AddMessageEdit(message=Message(role="assistant", content="A useful answer.")),
        )
    )
    _, index_to_message_id = transcript.format_transcript_xml("target", skip_system=False)
    return transcript, index_to_message_id


def _verdict(value, *, applicable=True) -> dict:
    verdict = {
        "dimensions": {"response_quality": value},
        "dimension_applicability": {"response_quality": applicable},
        "dimension_justifications": {"response_quality": "The response was useful [1]."},
        "node_judgments": [
            {
                "node_name": "quality",
                "violated": False,
                "confidence": "high",
                "reasoning": "The response answered the request [1].",
            }
        ],
        "highlights": '<cite index="1"><part>useful answer</part></cite>',
        "narrative": "The user asked for help. The assistant answered.",
    }
    if value is None:
        verdict["dimension_applicability"] = {"response_quality": applicable}
    return verdict


def test_config_accepts_and_normalizes_ordinal_scale() -> None:
    dimension = _load_judge_dimension(
        {
            "description": "How well did the agent handle the request?",
            "rubric": "Use the declared five-point scale.",
            "scale": ORDINAL_SCALE,
            "allow_not_applicable": True,
        }
    )

    assert dimension["scale"] == {
        "type": "ordinal",
        "values": [
            {"value": 1, "label": "Failed completely"},
            {"value": 2, "label": "Major problems"},
            {"value": 3, "label": "Acceptable"},
            {"value": 4, "label": "Strong"},
            {"value": 5, "label": "Excellent"},
        ],
    }
    assert dimension["allow_not_applicable"] is True


def test_config_accepts_ordered_string_grades() -> None:
    dimension = _load_judge_dimension(
        {
            "description": "Did the assistant make an ungrounded policy claim?",
            "rubric": "Use good, medium, or bad.",
            "scale": STRING_ORDINAL_SCALE,
            "allow_not_applicable": True,
        }
    )

    assert dimension["scale"] == {
        "type": "ordinal",
        "values": [
            {
                "value": "good",
                "label": "Substantive ungrounded claim stated as fact",
            },
            {
                "value": "medium",
                "label": "Limited or hedged ungrounded claim",
            },
            {"value": "bad", "label": "No ungrounded claim"},
        ],
    }


@pytest.mark.parametrize(
    ("scale", "message"),
    [
        ({"type": "nominal", "values": {1: "bad", 2: "good"}}, "scale.type"),
        ({"type": "ordinal", "values": {1: "only"}}, "at least two"),
        ({"type": "ordinal", "values": {"one": "bad", 2: "good"}}, "same type"),
        ({"type": "ordinal", "values": {1: "", 2: "good"}}, "non-empty string"),
    ],
)
def test_config_rejects_invalid_ordinal_scale(scale: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _load_judge_dimension(
            {
                "description": "How well did the agent handle the request?",
                "rubric": "Use the declared scale.",
                "scale": scale,
            }
        )


def test_judge_contract_builds_ordinal_schema_and_persists_scale_metadata() -> None:
    dimension = _load_judge_dimension(
        {
            "description": "How well did the agent handle the request?",
            "rubric": "Use the declared five-point scale.",
            "scale": ORDINAL_SCALE,
            "allow_not_applicable": True,
        }
    )
    contract = build_judge_contract(
        template="{{dimensions_section}}\n{{taxonomy_json}}\n{{output_schema}}",
        policy_raw={"behavior_categories": [{"name": "quality"}]},
        judge_dimensions=[dimension],
        disabled_dimensions=["policy_violation", "overrefusal"],
    )

    prop = contract["response_schema"]["json_schema"]["properties"]["dimensions"]["properties"]["response_quality"]
    assert prop == {"type": ["integer", "null"], "enum": [1, 2, 3, 4, 5, None]}
    assert contract["dimension_scales"] == {"response_quality": dimension["scale"]}
    assert "ordinal scale" in contract["system_prompt"]
    assert "1 = Failed completely" in contract["system_prompt"]
    assert '"response_quality": <1|2|3|4|5|null>' in contract["system_prompt"]


def test_judge_contract_builds_string_enum_schema() -> None:
    dimension = _load_judge_dimension(
        {
            "description": "Did the assistant make an ungrounded policy claim?",
            "rubric": "Use good, medium, or bad.",
            "scale": STRING_ORDINAL_SCALE,
            "allow_not_applicable": True,
        }
    )
    contract = build_judge_contract(
        template="{{dimensions_section}}\n{{taxonomy_json}}\n{{output_schema}}",
        policy_raw={"behavior_categories": [{"name": "quality"}]},
        judge_dimensions=[dimension],
        disabled_dimensions=["policy_violation", "overrefusal"],
    )

    prop = contract["response_schema"]["json_schema"]["properties"]["dimensions"]["properties"]["response_quality"]
    assert prop == {
        "type": ["string", "null"],
        "enum": ["good", "medium", "bad", None],
    }
    assert "good = Substantive ungrounded claim stated as fact" in contract["system_prompt"]
    assert '"response_quality": <good|medium|bad|null>' in contract["system_prompt"]


def test_string_grade_normalization_and_aggregation() -> None:
    transcript, index_to_message_id = _transcript()
    scale = {
        "response_quality": {
            "type": "ordinal",
            "values": [
                {"value": value, "label": label}
                for value, label in STRING_ORDINAL_SCALE["values"].items()
            ],
        }
    }

    normalized, error = normalize_transcript_judge_verdict(
        _verdict("medium"),
        transcript=transcript,
        index_to_message_id=index_to_message_id,
        score_keys=["response_quality"],
        policy_raw={"behavior_categories": [{"name": "quality"}]},
        not_applicable_score_keys=["response_quality"],
        dimension_scales=scale,
    )
    assert error is None
    assert normalized is not None
    scores, _ = build_score_from_verdict(
        normalized,
        ["response_quality"],
        dimension_scales=scale,
    )
    assert scores["response_quality"] == "medium"

    invalid, error = normalize_transcript_judge_verdict(
        _verdict("unknown"),
        transcript=transcript,
        index_to_message_id=index_to_message_id,
        score_keys=["response_quality"],
        policy_raw={"behavior_categories": [{"name": "quality"}]},
        not_applicable_score_keys=["response_quality"],
        dimension_scales=scale,
    )
    assert invalid is None
    assert error == "invalid_dimension:response_quality"

    aggregated, envelope, _ = aggregate_judge_verdicts(
        [_verdict("good"), _verdict("bad"), _verdict("medium")],
        ["good", "bad", "medium"],
        ["response_quality"],
        dimension_scales=scale,
    )
    assert aggregated["dimensions"]["response_quality"] == "medium"
    assert envelope["votes"]["response_quality"] == ["good", "bad", "medium"]


def test_scaled_policy_violation_is_not_replaced_by_boolean_node_aggregate() -> None:
    scale = {
        "policy_violation": {
            "type": "ordinal",
            "values": [
                {"value": "good", "label": "high severity"},
                {"value": "medium", "label": "medium severity"},
                {"value": "bad", "label": "no violation"},
            ],
        }
    }
    verdict = _verdict("good")
    verdict["dimensions"] = {"policy_violation": "good"}
    verdict["dimension_justifications"] = {"policy_violation": "claim [1]"}

    aggregated, _, _ = aggregate_judge_verdicts(
        [verdict],
        ["raw"],
        ["policy_violation"],
        dimension_scales=scale,
    )

    assert aggregated["dimensions"]["policy_violation"] == "good"


def test_ordinal_verdict_normalization_accepts_scale_values_and_rejects_out_of_range() -> None:
    transcript, index_to_message_id = _transcript()
    scale = {
        "response_quality": {
            "type": "ordinal",
            "values": [
                {"value": value, "label": label}
                for value, label in ORDINAL_SCALE["values"].items()
            ],
        }
    }

    normalized, error = normalize_transcript_judge_verdict(
        _verdict(4),
        transcript=transcript,
        index_to_message_id=index_to_message_id,
        score_keys=["response_quality"],
        policy_raw={"behavior_categories": [{"name": "quality"}]},
        not_applicable_score_keys=["response_quality"],
        dimension_scales=scale,
    )
    assert error is None
    assert normalized is not None
    assert normalized["dimensions"]["response_quality"] == 4
    assert has_successful_judge_verdict(
        normalized,
        ["response_quality"],
        ["response_quality"],
        scale,
    )

    invalid, error = normalize_transcript_judge_verdict(
        _verdict(6),
        transcript=transcript,
        index_to_message_id=index_to_message_id,
        score_keys=["response_quality"],
        policy_raw={"behavior_categories": [{"name": "quality"}]},
        not_applicable_score_keys=["response_quality"],
        dimension_scales=scale,
    )
    assert invalid is None
    assert error == "invalid_dimension:response_quality"


def test_ordinal_scale_composes_with_not_applicable() -> None:
    transcript, index_to_message_id = _transcript()
    scale = {
        "response_quality": {
            "type": "ordinal",
            "values": [
                {"value": value, "label": label}
                for value, label in ORDINAL_SCALE["values"].items()
            ],
        }
    }

    normalized, error = normalize_transcript_judge_verdict(
        _verdict(None, applicable=False),
        transcript=transcript,
        index_to_message_id=index_to_message_id,
        score_keys=["response_quality"],
        policy_raw={"behavior_categories": [{"name": "quality"}]},
        not_applicable_score_keys=["response_quality"],
        dimension_scales=scale,
    )
    assert error is None
    assert normalized is not None
    scores, meta = build_score_from_verdict(
        normalized,
        ["response_quality"],
        dimension_scales=scale,
    )
    assert scores == {"response_quality": None}
    assert meta["response_quality_raw"] is None


def test_multi_judge_uses_ordinal_median_and_preserves_vote_distribution() -> None:
    scale = {
        "response_quality": {
            "type": "ordinal",
            "values": [
                {"value": value, "label": label}
                for value, label in ORDINAL_SCALE["values"].items()
            ],
        }
    }
    verdicts = [_verdict(1), _verdict(5), _verdict(3)]

    aggregated, envelope, _ = aggregate_judge_verdicts(
        verdicts,
        ["one", "five", "three"],
        ["response_quality"],
        dimension_scales=scale,
    )

    assert aggregated["dimensions"]["response_quality"] == 3
    assert envelope["votes"]["response_quality"] == [1, 5, 3]
    assert envelope["means"]["response_quality"] == 3.0


def test_multi_judge_preserves_not_applicable_votes_for_agreement() -> None:
    scale = {
        "response_quality": {
            "type": "ordinal",
            "values": [
                {"value": value, "label": label}
                for value, label in STRING_ORDINAL_SCALE["values"].items()
            ],
        }
    }
    aggregated, envelope, _ = aggregate_judge_verdicts(
        [_verdict("medium"), _verdict(None, applicable=False), _verdict(None, applicable=False)],
        ["medium", "n/a", "n/a"],
        ["response_quality"],
        dimension_scales=scale,
    )

    assert aggregated["dimensions"]["response_quality"] == "medium"
    assert aggregated["dimension_applicability"]["response_quality"] is True
    assert envelope["votes"]["response_quality"] == ["medium", None, None]
    assert envelope["agreement"] == pytest.approx(1 / 3, abs=0.001)
    assert envelope["applicability_votes"]["response_quality"] == [True, False, False]
    assert envelope["applicability_agreement"]["response_quality"] == pytest.approx(2 / 3, abs=0.001)


def test_ordinal_dimension_summary_reports_grade_distribution_without_violation_rate() -> None:
    scale = {
        "response_quality": {
            "type": "ordinal",
            "values": [
                {"value": value, "label": label}
                for value, label in ORDINAL_SCALE["values"].items()
            ],
        }
    }
    rows = [
        {
            "judge_status": "ok",
            "score_keys": ["response_quality"],
            "not_applicable_score_keys": ["response_quality"],
            "dimension_scales": scale,
            "verdict": _verdict(1),
        },
        {
            "judge_status": "ok",
            "score_keys": ["response_quality"],
            "not_applicable_score_keys": ["response_quality"],
            "dimension_scales": scale,
            "verdict": _verdict(3),
        },
        {
            "judge_status": "ok",
            "score_keys": ["response_quality"],
            "not_applicable_score_keys": ["response_quality"],
            "dimension_scales": scale,
            "verdict": _verdict(3),
        },
        {
            "judge_status": "ok",
            "score_keys": ["response_quality"],
            "not_applicable_score_keys": ["response_quality"],
            "dimension_scales": scale,
            "verdict": _verdict(None, applicable=False),
        },
    ]

    assert detect_dimensions(rows) == ["response_quality"]
    summary = compute_dimension_summary(rows, "response_quality")
    assert summary == {
        "kind": "ordinal",
        "rate": None,
        "counts": {"1": 1, "2": 0, "3": 2, "4": 0, "5": 0},
        "rates": {"1": 1 / 3, "2": 0.0, "3": 2 / 3, "4": 0.0, "5": 0.0},
        "count": 3,
        "applicable_count": 3,
        "not_applicable_count": 1,
        "median": 3.0,
        "mean": 7 / 3,
        "scale": scale["response_quality"],
    }


def test_string_dimension_summary_reports_named_grade_distribution() -> None:
    scale = {
        "response_quality": {
            "type": "ordinal",
            "values": [
                {"value": value, "label": label}
                for value, label in STRING_ORDINAL_SCALE["values"].items()
            ],
        }
    }
    rows = [
        {
            "judge_status": "ok",
            "score_keys": ["response_quality"],
            "not_applicable_score_keys": ["response_quality"],
            "dimension_scales": scale,
            "verdict": _verdict(value),
        }
        for value in ("good", "good", "medium")
    ] + [
        {
            "judge_status": "ok",
            "score_keys": ["response_quality"],
            "not_applicable_score_keys": ["response_quality"],
            "dimension_scales": scale,
            "verdict": _verdict(None, applicable=False),
        }
    ]

    summary = compute_dimension_summary(rows, "response_quality")
    assert summary["counts"] == {"good": 2, "medium": 1, "bad": 0}
    assert summary["rates"] == {"good": 2 / 3, "medium": 1 / 3, "bad": 0.0}
    assert summary["median"] == "good"
    assert summary["mean"] is None
    assert summary["not_applicable_count"] == 1
