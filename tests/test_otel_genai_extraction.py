# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Regression tests for GenAI fallbacks in the direct extraction helpers (#241).

PR #238 added gen_ai.* support to the event-stream path; this covers Layer B
(extract_span_inputs / extract_trajectory_inputs / extract_session_inputs and
SpanNode.to_dict), ensuring pure-GenAI spans no longer extract blank fields
while OpenInference precedence is preserved for dual-emitting spans.
"""

import json

from assert_ai.core.otel import (
    OTelSpan,
    SpanNode,
    extract_session_inputs,
    extract_span_inputs,
    extract_trajectory_inputs,
)


def _span(kind, attributes, *, span_id="s1", trace_id="t1", parent=None, name="span"):
    return OTelSpan(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent,
        name=name,
        kind=kind,
        start_time_ns=1_000_000,
        end_time_ns=2_000_000,
        attributes=attributes,
    )


def _genai_llm_attrs():
    return {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": "gpt-4o",
        "gen_ai.usage.input_tokens": 11,
        "gen_ai.usage.output_tokens": 7,
        "gen_ai.input.messages": json.dumps([{"role": "user", "content": "What is 2+2?"}]),
        "gen_ai.output.messages": json.dumps([{"role": "assistant", "content": "4"}]),
    }


def test_pure_genai_llm_span_is_extracted():
    rows = extract_span_inputs([_span("LLM", _genai_llm_attrs())])
    assert len(rows) == 1
    r = rows[0]
    assert r["query"] == "What is 2+2?"
    assert r["response"] == "4"
    assert r["model"] == "gpt-4o"
    assert r["input_tokens"] == 11
    assert r["output_tokens"] == 7


def test_openinference_precedence_for_dual_emitting_span():
    attrs = _genai_llm_attrs()
    attrs.update({
        "openinference.span.kind": "LLM",
        "input.value": "OI-INPUT",
        "output.value": "OI-OUTPUT",
        "llm.model_name": "oi-model",
    })
    r = extract_span_inputs([_span("LLM", attrs)])[0]
    # OpenInference wins when openinference.span.kind is present
    assert r["query"] == "OI-INPUT"
    assert r["response"] == "OI-OUTPUT"
    assert r["model"] == "oi-model"


def test_openinference_only_span_unchanged():
    attrs = {
        "openinference.span.kind": "LLM",
        "input.value": "hi",
        "output.value": "hello",
        "llm.model_name": "m",
        "llm.token_count.prompt": 3,
        "llm.token_count.completion": 2,
    }
    r = extract_span_inputs([_span("LLM", attrs)])[0]
    assert r["query"] == "hi" and r["response"] == "hello"
    assert r["model"] == "m" and r["input_tokens"] == 3 and r["output_tokens"] == 2


def test_genai_tool_span_extraction():
    attrs = {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": "search",
        "gen_ai.tool.call.arguments": json.dumps({"q": "weather"}),
        "gen_ai.tool.call.result": json.dumps({"temp": 21}),
    }
    traj = extract_trajectory_inputs([
        _span("LLM", _genai_llm_attrs(), span_id="a"),
        _span("TOOL", attrs, span_id="b"),
    ])[0]
    calls = json.loads(traj["tool_calls"])
    assert calls == [{"name": "search", "arguments": {"q": "weather"}}]
    assert traj["user_input"] == "What is 2+2?"
    assert traj["total_tokens"] == {"input": 11, "output": 7}


def test_genai_session_extraction():
    sess_attrs = _genai_llm_attrs()
    sess_attrs["session.id"] = "sess-1"
    r = extract_session_inputs([_span("LLM", sess_attrs)])[0]
    assert json.loads(r["user_inputs"]) == ["What is 2+2?"]
    assert json.loads(r["output_messages"]) == ["4"]


def test_genai_span_tree_serialization():
    node = SpanNode(span=_span("LLM", _genai_llm_attrs()))
    d = node.to_dict(include_input=True, include_output=True)
    assert d["model"] == "gpt-4o"
    assert d["tokens"] == {"input": 11, "output": 7}
    assert d["input"] == "What is 2+2?"
    assert d["output"] == "4"
