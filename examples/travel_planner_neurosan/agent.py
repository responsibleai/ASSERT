# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Custom-instrumented multi-agent travel planner — manual OTel spans.

Unlike the phoenix_auto_trace demos (which use Phoenix's auto-instrumentors),
this demo shows that ANY custom orchestration can produce OTel traces that
ASSERT's eval pipeline understands. No framework required — just OpenTelemetry.

Architecture:
    coordinator → intent_classifier → flight_searcher → hotel_searcher
                                   → safety_advisor → itinerary_optimizer

Each "agent" is a plain Python function wrapped in a manual OTel span.
The spans follow OpenInference semantic conventions so assert_eval/core/otel.py
can parse them into transcript events for the judge.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

from dotenv import load_dotenv
load_dotenv()

import litellm
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from examples.phoenix_auto_trace._tools import simulate_tool, SYSTEM_PROMPT

# ── OTel setup (manual spans, no auto-instrumentor) ───────────
# Create a TracerProvider if none exists. LiveOTelExporter will attach
# its collector to this provider when the eval pipeline runs.

_existing = trace.get_tracer_provider()
if not isinstance(_existing, TracerProvider):
    _real = getattr(_existing, "_real_provider", None)
    if not isinstance(_real, TracerProvider):
        _provider = TracerProvider()
        trace.set_tracer_provider(_provider)

_tracer = trace.get_tracer("travel_planner_neurosan")

_MODEL = os.environ.get("ASSERT_TARGET_MODEL", "azure/gpt-5.4-mini")


# ── Agent functions (each manually instrumented) ──────────────

def _llm_call(system: str, user: str, span_name: str) -> str:
    """Make an LLM call and record it as an OTel span."""
    with _tracer.start_as_current_span(span_name) as span:
        span.set_attribute("openinference.span.kind", "LLM")
        span.set_attribute("llm.model_name", _MODEL)
        span.set_attribute("input.value", user)

        response = litellm.completion(
            model=_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0,
            max_tokens=4000,
        )
        text = response.choices[0].message.content or ""
        usage = response.usage
        if usage:
            span.set_attribute("llm.token_count.prompt", usage.prompt_tokens)
            span.set_attribute("llm.token_count.completion", usage.completion_tokens)
        span.set_attribute("output.value", text)
        return text


def _tool_call(tool_name: str, args: dict[str, Any]) -> str:
    """Execute a tool and record it as an OTel span."""
    with _tracer.start_as_current_span(f"tool:{tool_name}") as span:
        span.set_attribute("openinference.span.kind", "TOOL")
        span.set_attribute("tool.name", tool_name)
        span.set_attribute("input.value", json.dumps(args))
        result = simulate_tool(tool_name, args)
        span.set_attribute("output.value", result)
        return result


def classify_intent(message: str) -> dict[str, Any]:
    """Agent 1: Classify travel intent and extract parameters."""
    with _tracer.start_as_current_span("intent_classifier") as span:
        span.set_attribute("openinference.span.kind", "AGENT")
        raw = _llm_call(
            system=(
                "Extract travel parameters as JSON: "
                '{"destination": str, "country": str, "days": int, "budget": float}. '
                "Return ONLY valid JSON."
            ),
            user=message,
            span_name="intent_classifier.llm",
        )
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"destination": "Tokyo", "country": "Japan", "days": 7, "budget": 3000}
        span.set_attribute("output.value", json.dumps(parsed))
        return parsed


def search_flights(destination: str) -> str:
    """Agent 2: Search flights."""
    with _tracer.start_as_current_span("flight_searcher") as span:
        span.set_attribute("openinference.span.kind", "AGENT")
        results = _tool_call("search_flights", {"destination": destination})
        summary = _llm_call(
            system="Summarize the flight options concisely.",
            user=f"Flight results: {results}",
            span_name="flight_searcher.llm",
        )
        span.set_attribute("output.value", summary)
        return summary


def search_hotels(destination: str) -> str:
    """Agent 3: Search hotels."""
    with _tracer.start_as_current_span("hotel_searcher") as span:
        span.set_attribute("openinference.span.kind", "AGENT")
        results = _tool_call("search_hotels", {"city": destination})
        summary = _llm_call(
            system="Summarize the hotel options concisely.",
            user=f"Hotel results: {results}",
            span_name="hotel_searcher.llm",
        )
        span.set_attribute("output.value", summary)
        return summary


def check_safety(destination: str, country: str) -> str:
    """Agent 4: Check weather and advisories."""
    with _tracer.start_as_current_span("safety_advisor") as span:
        span.set_attribute("openinference.span.kind", "AGENT")
        weather = _tool_call("check_weather", {"city": destination})
        advisories = _tool_call("check_travel_advisories", {"country": country})
        summary = _llm_call(
            system="Summarize weather and safety information concisely.",
            user=f"Weather: {weather}\nAdvisories: {advisories}",
            span_name="safety_advisor.llm",
        )
        span.set_attribute("output.value", summary)
        return summary


def optimize_itinerary(
    message: str, flights: str, hotels: str, safety: str, budget: float
) -> str:
    """Agent 5: Create final itinerary."""
    with _tracer.start_as_current_span("itinerary_optimizer") as span:
        span.set_attribute("openinference.span.kind", "AGENT")
        budget_check = _tool_call("validate_budget", {
            "flight_cost": 850, "hotel_cost": 770, "other_costs": 200, "budget": budget,
        })
        result = _llm_call(
            system=SYSTEM_PROMPT,
            user=(
                f"Original request: {message}\n\n"
                f"Flights:\n{flights}\n\n"
                f"Hotels:\n{hotels}\n\n"
                f"Safety:\n{safety}\n\n"
                f"Budget check: {budget_check}\n\n"
                "Create a complete itinerary."
            ),
            span_name="itinerary_optimizer.llm",
        )
        span.set_attribute("output.value", result)
        return result


# ── Coordinator ───────────────────────────────────────────────

def chat(message: str) -> str:
    """Main entry point — orchestrates all agents with manual OTel spans."""
    with _tracer.start_as_current_span("coordinator") as span:
        span.set_attribute("openinference.span.kind", "CHAIN")
        span.set_attribute("input.value", message)

        intent = classify_intent(message)
        dest = intent.get("destination", "Tokyo")
        country = intent.get("country", "Japan")
        budget = intent.get("budget", 3000)

        flights = search_flights(dest)
        hotels = search_hotels(dest)
        safety = check_safety(dest, country)
        result = optimize_itinerary(message, flights, hotels, safety, budget)

        span.set_attribute("output.value", result)
        return result


if __name__ == "__main__":
    print(chat("Plan a week in Tokyo for under $3000"))
