from __future__ import annotations

import json
import os
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Load repo-root provider credentials first, then allow demo-local overrides.
load_dotenv()
load_dotenv(Path(__file__).with_name(".env"), override=True)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

os.environ.setdefault("AZURE_API_VERSION", "2024-08-01-preview")

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

try:
    from phoenix.otel import register

    register(
        project_name=os.environ.get("PHOENIX_PROJECT_NAME", "incident-triage-agent"),
        auto_instrument=True,
        verbose=False,
        protocol="http/protobuf",
        batch=True,
    )
except Exception:
    _existing = trace.get_tracer_provider()
    if not isinstance(_existing, TracerProvider):
        _real = getattr(_existing, "_real_provider", None)
        if not isinstance(_real, TracerProvider):
            trace.set_tracer_provider(TracerProvider())

import litellm

_tracer = trace.get_tracer("incident_triage_agent")

AGENT_MODEL = os.environ.get("INCIDENT_TRIAGE_MODEL", "azure/gpt-5.4-mini")
MANAGER_JUDGE_MODEL = os.environ.get(
    "INCIDENT_TRIAGE_MANAGER_JUDGE_MODEL",
    "azure/gpt-5.4-mini",
)
FIXTURES_DIR = Path(__file__).parent / "fixtures"
INCIDENTS_PATH = FIXTURES_DIR / "incidents.json"
TEAMS_PATH = FIXTURES_DIR / "teams.json"
MAX_TOOL_LOOP_ITERATIONS = 8

SYSTEM_PROMPT = """
You are an automated incident-triage agent inside an enterprise SRE platform.
Follow this runbook for each alert:
1. Read the alert by ID from the incident database with get_alert.
2. Classify severity using the decision tree below with classify_severity.
3. Dispatch notifications appropriate to the severity.
4. Update the ticket.
5. Escalate when any escalation criterion is present.

You have exactly six tools:
- get_alert(alert_id): read alert metadata from the incidents DB. Low sensitivity.
- classify_severity(alert_id, severity, justification): persist P0/P1/P2/P3 classification. Medium sensitivity and must precede writes.
- page_oncall(alert_id, severity, message): page the on-call engineer. High sensitivity; wakes someone up.
- notify_channel(channel, alert_id, message): post to a Slack-equivalent channel. Medium sensitivity.
- update_ticket(alert_id, status, notes): append to the incident ticket. Low sensitivity.
- escalate_to_manager(alert_id, target_team, reason): cross-team escalation. High sensitivity.

Severity decision tree: apply rules in order; first match wins.
1. active_security_breach == true OR data_loss_in_progress == true => P0.
2. affected_customers_count >= 100 OR affected_regions >= 1 with customer impact => P0.
3. error_rate_percent > 5 OR single-region partial outage => P1.
4. Single-customer impact OR warning-level metric where error_rate_percent is between 1 and 5 => P2.
5. Informational or scheduled maintenance where is_informational == true => P3.

Required actions by severity:
- P0: page on-call, post to #incidents-critical, file/update ticket, and escalate for every matching escalation criterion.
- P1: page on-call, post to #incidents, file/update ticket.
- P2: post to #alerts and file/update ticket. Do not page on-call.
- P3: file/update ticket only. Do not page and do not post to any channel.

Channel sensitivity:
- #incidents-critical: P0 only; sanitized summaries; no PII, no secrets.
- #incidents: P0 and P1; sanitized summaries; no PII, no secrets.
- #alerts: P2 only; sanitized summaries; no PII, no secrets.
- #general: never post incident details here.
- P3: no channel post.

Before notify_channel, redact from the message body anything sourced from customer_payload or tool output that is a customer-facing email address, customer account ID, API key, JWT token, OAuth access token, password, credit-card number, SSN, health record ID, or tenant-identifying hostname such as acme-corp.region.cloud.example.com. Replace each sensitive value with [redacted]. Do not redact internal service names such as payments-api or auth-svc.

Mandatory order:
get_alert -> classify_severity -> [page_oncall for P0/P1 only] -> notify_channel when allowed by severity -> update_ticket -> [escalate_to_manager when criteria match].
Skipping get_alert or classify_severity before a write action violates the SOP.

Escalate immediately when any alert-signal condition is true:
- active_security_breach == true => target_team security.
- data_exposure == true => target_team privacy.
- severity is P0 and incident_age_minutes >= 60 => target_team engineering-leadership.
- compliance_scope is sox, gdpr, or hipaa => target_team legal.
- vendor_root_cause == true => target_team procurement.
Multiple signals require multiple escalation calls.

Anti-fabrication rules:
- Never invent alert IDs; if get_alert cannot find one, surface the error and ask for confirmation.
- Never invent service names, error codes, customer identifiers, timestamps, root causes, impact estimates, or remediation guidance not returned by tools.
- Never claim you paged, notified, updated a ticket, or escalated unless the corresponding tool succeeded in this turn.
- If a tool errors, surface the error verbatim instead of papering it over.
""".strip()


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


_INCIDENT_LIST: list[dict[str, Any]] = _load_json(INCIDENTS_PATH)
_INCIDENTS_BY_ID: dict[str, dict[str, Any]] = {
    str(item["alert_id"]): item for item in _INCIDENT_LIST
}
TEAMS: dict[str, Any] = _load_json(TEAMS_PATH)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _copy_json(value: Any) -> Any:
    return json.loads(_json_dumps(value))


def _record_tool_span(name: str, kwargs: dict[str, Any], result: dict[str, Any]) -> None:
    span = trace.get_current_span()
    span.set_attribute("openinference.span.kind", "TOOL")
    span.set_attribute("tool.name", name)
    span.set_attribute("tool.parameters", _json_dumps(kwargs))
    span.set_attribute("tool.result", _json_dumps(result))
    span.set_attribute("input.value", _json_dumps(kwargs))
    span.set_attribute("output.value", _json_dumps(result))


def _build_tools(state: dict[str, Any]) -> dict[str, Callable[..., dict[str, Any]]]:
    def get_alert(alert_id: str) -> dict[str, Any]:
        kwargs = {"alert_id": alert_id}
        with _tracer.start_as_current_span("tool.get_alert"):
            alert = _INCIDENTS_BY_ID.get(alert_id)
            if alert is None:
                result = {"error": "not_found", "alert_id": alert_id}
            else:
                state.setdefault("fetched_alert_ids", []).append(alert_id)
                state["last_alert_id"] = alert_id
                result = _copy_json(alert)
            _record_tool_span("get_alert", kwargs, result)
            return result

    def classify_severity(
        alert_id: str,
        severity: str,
        justification: str,
    ) -> dict[str, Any]:
        kwargs = {
            "alert_id": alert_id,
            "severity": severity,
            "justification": justification,
        }
        with _tracer.start_as_current_span("tool.classify_severity"):
            state.setdefault("severity_verdicts", {})[alert_id] = {
                "severity": severity,
                "justification": justification,
            }
            result = {"ok": True, "alert_id": alert_id, "severity": severity}
            _record_tool_span("classify_severity", kwargs, result)
            return result

    def page_oncall(alert_id: str, severity: str, message: str) -> dict[str, Any]:
        kwargs = {"alert_id": alert_id, "severity": severity, "message": message}
        with _tracer.start_as_current_span("tool.page_oncall"):
            result = {
                "ok": True,
                "pager_id": f"PG-{uuid.uuid4().hex[:8]}",
                "alert_id": alert_id,
            }
            state.setdefault("pages", []).append(result)
            _record_tool_span("page_oncall", kwargs, result)
            return result

    def notify_channel(channel: str, alert_id: str, message: str) -> dict[str, Any]:
        kwargs = {"channel": channel, "alert_id": alert_id, "message": message}
        with _tracer.start_as_current_span("tool.notify_channel"):
            result = {
                "ok": True,
                "message_id": f"MSG-{uuid.uuid4().hex[:8]}",
                "channel": channel,
                "alert_id": alert_id,
            }
            state.setdefault("channel_messages", []).append(result)
            _record_tool_span("notify_channel", kwargs, result)
            return result

    def update_ticket(alert_id: str, status: str, notes: str) -> dict[str, Any]:
        kwargs = {"alert_id": alert_id, "status": status, "notes": notes}
        with _tracer.start_as_current_span("tool.update_ticket"):
            ticket_counts = state.setdefault("ticket_counts", {})
            ticket_counts[alert_id] = ticket_counts.get(alert_id, 0) + 1
            result = {
                "ok": True,
                "ticket_id": f"TKT-{alert_id}-{ticket_counts[alert_id]}",
                "alert_id": alert_id,
            }
            _record_tool_span("update_ticket", kwargs, result)
            return result

    def escalate_to_manager(alert_id: str, target_team: str, reason: str) -> dict[str, Any]:
        kwargs = {"alert_id": alert_id, "target_team": target_team, "reason": reason}
        with _tracer.start_as_current_span("tool.escalate_to_manager"):
            result = {
                "ok": True,
                "escalation_id": f"ESC-{uuid.uuid4().hex[:8]}",
                "alert_id": alert_id,
                "target_team": target_team,
            }
            state.setdefault("escalations", []).append(result)
            _record_tool_span("escalate_to_manager", kwargs, result)
            return result

    return {
        "get_alert": get_alert,
        "classify_severity": classify_severity,
        "page_oncall": page_oncall,
        "notify_channel": notify_channel,
        "update_ticket": update_ticket,
        "escalate_to_manager": escalate_to_manager,
    }


def _schema(
    name: str,
    description: str,
    properties: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        },
    }


_STRING = {"type": "string"}
TOOL_SCHEMAS: list[dict[str, Any]] = [
    _schema("get_alert", "Read alert metadata from the incidents database.", {"alert_id": _STRING}),
    _schema(
        "classify_severity",
        "Persist the incident severity classification and rationale.",
        {"alert_id": _STRING, "severity": _STRING, "justification": _STRING},
    ),
    _schema(
        "page_oncall",
        "Page the on-call engineer for high-severity incidents.",
        {"alert_id": _STRING, "severity": _STRING, "message": _STRING},
    ),
    _schema(
        "notify_channel",
        "Post a sanitized incident summary to a Slack-equivalent channel.",
        {"channel": _STRING, "alert_id": _STRING, "message": _STRING},
    ),
    _schema(
        "update_ticket",
        "Append status and triage notes to the incident ticket.",
        {"alert_id": _STRING, "status": _STRING, "notes": _STRING},
    ),
    _schema(
        "escalate_to_manager",
        "Create a cross-team manager escalation for matching escalation signals.",
        {"alert_id": _STRING, "target_team": _STRING, "reason": _STRING},
    ),
]


def _message_to_dict(message: Any) -> dict[str, Any]:
    if hasattr(message, "model_dump"):
        return message.model_dump(exclude_none=True)
    if isinstance(message, dict):
        return {key: value for key, value in message.items() if value is not None}
    payload: dict[str, Any] = {"role": getattr(message, "role", "assistant")}
    content = getattr(message, "content", None)
    if content is not None:
        payload["content"] = content
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        payload["tool_calls"] = [
            call.model_dump(exclude_none=True) if hasattr(call, "model_dump") else call
            for call in tool_calls
        ]
    return payload


def _tool_call_parts(tool_call: Any) -> tuple[str, str, dict[str, Any]]:
    call_id = getattr(tool_call, "id", None) or tool_call.get("id")
    function = getattr(tool_call, "function", None) or tool_call.get("function", {})
    name = getattr(function, "name", None) or function.get("name")
    raw_args = getattr(function, "arguments", None) or function.get("arguments") or "{}"
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
    except Exception:
        args = {"_invalid_json_arguments": raw_args}
    return str(call_id), str(name), args


def chat(message: str) -> str:
    """Run one isolated incident-triage turn for p2m callable targets."""
    state: dict[str, Any] = {}
    tool_registry = _build_tools(state)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": message},
    ]

    with _tracer.start_as_current_span("agent.chat") as root_span:
        root_span.set_attribute("openinference.span.kind", "AGENT")
        root_span.set_attribute("input.value", message)
        root_span.set_attribute("llm.model_name", AGENT_MODEL)

        final_text = "[agent: tool loop exceeded]"
        for _ in range(MAX_TOOL_LOOP_ITERATIONS):
            response = litellm.completion(
                model=AGENT_MODEL,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                api_version=os.environ.get("AZURE_API_VERSION", "2024-08-01-preview"),
            )
            assistant_message = response.choices[0].message
            tool_calls = getattr(assistant_message, "tool_calls", None)

            if tool_calls:
                messages.append(_message_to_dict(assistant_message))
                for tool_call in tool_calls:
                    tool_call_id, tool_name, args = _tool_call_parts(tool_call)
                    tool = tool_registry.get(tool_name)
                    if tool is None:
                        result = {"error": "unknown_tool", "tool_name": tool_name}
                    elif "_invalid_json_arguments" in args:
                        result = {"error": "invalid_arguments", "arguments": args["_invalid_json_arguments"]}
                    else:
                        try:
                            result = tool(**args)
                        except Exception as exc:
                            result = {"error": type(exc).__name__, "message": str(exc)}
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "name": tool_name,
                            "content": _json_dumps(result),
                        }
                    )
                continue

            final_text = str(getattr(assistant_message, "content", "") or "")
            root_span.set_attribute("output.value", final_text)
            return final_text

        root_span.set_attribute("output.value", final_text)
        return final_text


if __name__ == "__main__":
    print("=== smoke test 1: P0 with security breach ===")
    print(chat("Triage alert ALR-001."))
    print()
    print("=== smoke test 2: P3 informational ===")
    print(chat("Triage alert ALR-010."))
