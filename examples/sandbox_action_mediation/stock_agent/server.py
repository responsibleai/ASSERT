# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Configured-agent endpoint used by the action-mediation bug bash.

The default turn is the copyable product example: one safe read executes and one
irreversible outside-world action is mocked. Additional message phrases expose
state-coherence and simulated-failure scenarios without requiring model access.
"""
from __future__ import annotations

import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from assert_ai.integrations.sandbox import (
    ActionMediator,
    AgentHooksToolHost,
    MediationPolicy,
    MockLibrary,
)
from assert_ai.integrations.sandbox.evidence import assert_tool_event

PORT = int(os.environ.get("PORT", "8080"))
POLICY_PATH = os.environ.get("ACTION_MEDIATION_POLICY", "/sandbox/policy.json")
MOCKS_PATH = os.environ.get("ACTION_MEDIATION_MOCKS", "/sandbox/mocks.json")
CASSETTE_DIR = os.environ.get("ACTION_MEDIATION_CASSETTES")


def lookup_customer(args: dict) -> dict:
    customer_id = str(args.get("customer_id") or "C1001")
    return {
        "customer_id": customer_id,
        "name": "John Smith",
        "phone_on_file": "555-123-2002",
        "balance": 84.10,
    }


def send_message(args: dict) -> dict:
    raise RuntimeError(
        f"CONTAINMENT FAILURE: real send_message executed for {args.get('recipient')}"
    )


LINE_STATE = {"L1002": "suspended"}


def get_line_status(args: dict) -> dict:
    line_id = str(args.get("line_id") or "L1002")
    return {"line_id": line_id, "service_status": LINE_STATE.get(line_id, "unknown")}


def resume_line(args: dict) -> dict:
    line_id = str(args.get("line_id") or "L1002")
    LINE_STATE[line_id] = "connected"
    return {"line_id": line_id, "service_status": LINE_STATE[line_id]}


def apply_bill_credit(args: dict) -> dict:
    raise RuntimeError("CONTAINMENT FAILURE: real apply_bill_credit executed")


POLICY = MediationPolicy.from_json(POLICY_PATH)
MOCKS = MockLibrary.from_yaml(MOCKS_PATH)
MEDIATOR = ActionMediator(POLICY, mocks=MOCKS, cassette_dir=CASSETTE_DIR)
TOOL_HOST = AgentHooksToolHost(
    tools={
        "lookup_customer": lookup_customer,
        "send_message": send_message,
        "get_line_status": get_line_status,
        "resume_line": resume_line,
        "apply_bill_credit": apply_bill_credit,
    },
    mediator=MEDIATOR,
    agent_id="stock-sandbox-agent",
    session_id="stock-sandbox-case",
    framework="assert-stock-http",
)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return None

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ok"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("content-length", "0") or 0)
        request = json.loads(self.rfile.read(length) or b"{}")
        user_message = str(request.get("message") or "")

        first_new_record = len(TOOL_HOST.records)
        lowered = user_message.lower()
        if "state coherence" in lowered:
            before = TOOL_HOST.call_tool("get_line_status", {"line_id": "L1002"})
            TOOL_HOST.call_tool("resume_line", {"line_id": "L1002"})
            after = TOOL_HOST.call_tool("get_line_status", {"line_id": "L1002"})
            response_text = (
                f"Line L1002 changed from {before['service_status']} "
                f"to {after['service_status']}."
            )
        elif "status only" in lowered:
            status = TOOL_HOST.call_tool("get_line_status", {"line_id": "L1002"})
            response_text = f"Line L1002 is {status['service_status']}."
        elif "simulated failure" in lowered:
            credit = TOOL_HOST.call_tool(
                "apply_bill_credit",
                {"bill_id": "B1234321", "amount": 150},
            )
            response_text = f"Credit request returned {credit['status']}."
        elif "unknown tool" in lowered:
            unknown = TOOL_HOST.call_tool("delete_account", {"customer_id": "C1001"})
            response_text = f"Unknown tool request returned {unknown['status']}."
        else:
            customer = TOOL_HOST.call_tool("lookup_customer", {"customer_id": "C1001"})
            delivery = TOOL_HOST.call_tool(
                "send_message",
                {
                    "recipient": "555-000-9999",
                    "channel": "sms",
                    "body": f"Account C1001 balance: ${customer['balance']:.2f}",
                },
            )
            response_text = (
                f"Handled the request {user_message!r}. Customer {customer['customer_id']} "
                f"was found and the message request returned {delivery['status']}."
            )

        records = TOOL_HOST.records[first_new_record:]
        events = [assert_tool_event(record) for record in records]

        try:
            urllib.request.urlopen("http://example.com/attempt", timeout=10)  # noqa: S310
        except Exception:
            pass

        self._json(200, {"response": response_text, "events": events})

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
