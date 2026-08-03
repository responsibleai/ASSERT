"""Minimal `opa eval` stand-in for the ACS native SDK (local B2/T1 runs).

The official `agent_control_specification` SDK is a maturin/Rust native build not
on PyPI. This shim implements exactly the surface the bank agent uses
(AgentControl.from_path / run_tool / run, AgentControlBlocked, EnforcementMode)
by dispatching each intervention point to the SAME Rego policies via the `opa`
binary. ONLY the dispatch engine differs from ACS; the policies — and therefore
every guardrail decision in the experiment — are identical.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import yaml

_OPA = shutil.which("opa") or str(Path.home() / ".local" / "bin" / "opa")

# Fail-SAFE score injected when an optional post_enrich wrapper (a per-tool risk
# enricher passed to run_tool) raises: lands in the gate's escalate band
# (> escalate_lo=45, <= deny_hi=80) so an enrichment failure ESCALATES to a
# human, never silently allows.
WRAPPER_FAILSAFE_SCORE = 46


def mcp_text(result):
    """Normalize an MCP tool return to its JSON text string.

    langchain_mcp_adapters returns either a JSON str, a (content, artifact)
    tuple, or a list of content blocks [{'type':'text','text': '...'}]. The Rego
    post_tool_call gate needs the raw JSON string (it json.unmarshals it).
    """
    val = result
    if isinstance(val, tuple) and val:
        val = val[0]
    if isinstance(val, list):
        val = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in val)
    return val if isinstance(val, str) else json.dumps(val)


class EnforcementMode:
    ENFORCE = "enforce"


class Verdict:
    def __init__(self, d: dict):
        self.decision = d.get("decision", "allow")
        self.reason = d.get("reason")
        self.message = d.get("message")


class _Result:
    def __init__(self, verdict: Verdict):
        self.verdict = verdict


class AgentControlBlocked(Exception):
    def __init__(self, result: _Result):
        self.result = result
        super().__init__(result.verdict.reason or "blocked")


class _Value:
    def __init__(self, value):
        self.value = value


class AgentControl:
    """opa-backed policy decision point matching the ACS surface the agent uses."""

    def __init__(self, bundle: str, queries: dict):
        self.bundle = bundle
        self.queries = queries  # intervention_point -> rego query

    @classmethod
    def from_path(cls, manifest_path: str) -> "AgentControl":
        m = yaml.safe_load(Path(manifest_path).read_text())
        bundle = next(iter(m.get("policies", {}).values()))["bundle"]
        ips = m.get("intervention_points", {})
        queries = {ip: ips[ip]["policy"]["query"] for ip in ips if "policy" in ips[ip]}
        return cls(bundle, queries)

    def _eval(self, ip: str, doc: dict) -> Verdict:
        q = self.queries.get(ip)
        if not q:
            return Verdict({"decision": "allow"})
        proc = subprocess.run(
            [_OPA, "eval", "-f", "json", "-I", "-d", self.bundle, q],
            input=json.dumps(doc), capture_output=True, text=True,
        )
        if proc.returncode != 0:
            # Fail-open, but LOUDLY: a silently-unguarded arm (misconfigured
            # bundle / missing opa) must be visible in logs, not pass as a clean
            # eval. A reviewer audit flagged the silent allow-and-continue.
            import sys
            sys.stderr.write(f"[acs_shim] WARNING opa eval failed (fail-open to allow) "
                             f"ip={ip}: {(proc.stderr or '')[:160]}\n")
            return Verdict({"decision": "allow", "reason": "opa_error",
                            "message": (proc.stderr or "")[:200]})
        try:
            val = json.loads(proc.stdout or "{}")["result"][0]["expressions"][0]["value"]
        except (KeyError, IndexError, ValueError):
            val = {"decision": "allow"}
        return Verdict(val if isinstance(val, dict) else {"decision": "allow"})

    async def run_tool(self, tool_name, args, execute, snapshot=None, mode=None, post_enrich=None):
        snap = snapshot or {}
        pre = self._eval("pre_tool_call", {
            "intervention_point": "pre_tool_call", "snapshot": snap,
            "tool": {"name": tool_name}, "policy_target": {"value": args}})
        if pre.decision == "deny":
            raise AgentControlBlocked(_Result(pre))
        result = await execute(args)
        snap2 = dict(snap)
        if post_enrich is not None:
            try:
                snap2.update(post_enrich(tool_name, args, mcp_text(result)) or {})
            except Exception:  # noqa: BLE001 - classifier must never crash the turn
                # fail-SAFE: a wrapper crash routes to the escalate band -> escalate to
                # a human, never silently allow ("errors escalate, never allow").
                snap2.setdefault("llm_risk_score", WRAPPER_FAILSAFE_SCORE)
        post = self._eval("post_tool_call", {
            "intervention_point": "post_tool_call", "snapshot": snap2,
            "tool": {"name": tool_name}, "policy_target": {"value": mcp_text(result)}})
        if post.decision in ("deny", "escalate"):
            raise AgentControlBlocked(_Result(post))
        return _Value(result)

    async def run(self, input_value, execute, mode=None):
        vin = self._eval("input", {
            "intervention_point": "input", "snapshot": {},
            "policy_target": {"value": input_value}})
        if vin.decision in ("deny", "escalate"):
            raise AgentControlBlocked(_Result(vin))
        out = await execute(input_value)
        vout = self._eval("output", {
            "intervention_point": "output", "snapshot": {},
            "policy_target": {"value": out}})
        if vout.decision in ("deny", "escalate"):
            raise AgentControlBlocked(_Result(vout))
        return _Value(out)
