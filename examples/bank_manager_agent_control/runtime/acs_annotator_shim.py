"""ACS annotator dispatch — the host side of ACS §10.

`acs_shim.AgentControl` evaluates the Rego intervention points via the `opa`
binary. It knows nothing about annotators. This module extends it with the one
piece ACS explicitly leaves to the host:

    "The runtime resolves each `from` path against the preliminary policy input
     and snapshot, then calls the host annotator dispatcher, which owns the
     network request, the classifier or judge call, caching, retries, and
     timeouts."  — ACS §10

So: the manifest declares `annotators: {coercion_risk: {type: classifier, ...}}`
once, each intervention point opts in via `annotations: {coercion_risk: {from:
...}}`, and this dispatcher resolves the path, calls the classifier, and places
the result at `annotations.coercion_risk` in the policy input — and nowhere
else. The Rego then reads it as ordinary data.

Fail-closed semantics from the spec are preserved: an annotator error or timeout
must not fall through to `allow`. `coercion_classifier.annotate` already
fail-safes into the escalate band; if the dispatcher itself raises, we emit an
annotation that lands in the escalate band rather than a missing annotation
(whose Rego defaults are unreachable, i.e. also non-allowing).
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml  # noqa: E402
from opentelemetry import trace  # noqa: E402

from .acs_shim import AgentControl as _BaseAgentControl  # noqa: E402
from .acs_shim import AgentControlBlocked, EnforcementMode, Verdict, _Result, _Value  # noqa: E402,F401
from .acs_shim import mcp_text as _mcp_text  # noqa: E402

from . import coercion_classifier as cc  # noqa: E402

_TRACER = trace.get_tracer("assert.bank_manager.acs")


def _resolve(path: str, doc: dict):
    """Resolve a `$.a.b.c` JSONPath-lite `from` expression against the policy input."""
    if not path or path in ("$", "$target"):
        return doc
    cur = doc
    for part in path.lstrip("$").lstrip(".").split("."):
        if not part:
            continue
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


class AnnotatingAgentControl(_BaseAgentControl):
    """`acs_shim.AgentControl` + annotator dispatch at each intervention point."""

    def __init__(self, bundle, queries, annotators=None, point_annotations=None, scorer=None):
        super().__init__(bundle, queries)
        self.annotators = annotators or {}
        self.point_annotations = point_annotations or {}
        self.scorer = scorer  # override the classifier (used by the naive-gate arm)
        self.trace: list[dict] = []  # every annotation + verdict, for audit

    @classmethod
    def from_path(cls, manifest_path, scorer=None):  # type: ignore[override]
        manifest_path = Path(manifest_path).resolve()
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        bundle = next(iter(manifest.get("policies", {}).values()))["bundle"]
        # ACS 0.1.0 resolves a relative `bundle:` against the process cwd, not the
        # manifest, and fails silently with policy_invocation_failed on Windows.
        # Same workaround agent.py uses.
        bundle = str((manifest_path.parent / bundle).resolve())
        points = manifest.get("intervention_points", {})
        queries = {ip: points[ip]["policy"]["query"] for ip in points if "policy" in points[ip]}
        point_annotations = {ip: points[ip].get("annotations", {}) for ip in points}
        return cls(bundle, queries, manifest.get("annotators", {}), point_annotations, scorer)

    def _annotate(self, ip: str, doc: dict) -> dict:
        """Run every annotator this intervention point opted into.

        Invoked in ascending lexicographic order of annotator name; output is
        placed ONLY under `annotations.<name>` (never shadowing snapshot,
        policy_target, tool or intervention_point).
        """
        requested = self.point_annotations.get(ip) or {}
        if not requested:
            return doc
        out = dict(doc)
        annotations: dict = {}
        for name in sorted(requested):
            decl = self.annotators.get(name)
            if decl is None:
                raise ValueError(f"annotation '{name}' names an undeclared annotator")
            from_path = (requested[name] or {}).get("from")
            if not from_path:
                raise ValueError(f"annotation '{name}' is missing a non-empty 'from' path")
            resolved = _resolve(from_path, doc)
            annotations[name] = self._dispatch(name, decl, resolved, doc)
        out["annotations"] = annotations
        self.trace.append({"intervention_point": ip,
                           "tool": doc.get("tool", {}).get("name"),
                           "annotations": annotations})
        return out

    def _dispatch(self, name: str, decl: dict, resolved, doc: dict) -> dict:
        """Host dispatcher. `type: classifier` -> the coercion classifier."""
        if decl.get("type") != "classifier":
            raise ValueError(f"unsupported annotator type {decl.get('type')!r}")
        tool_name = (doc.get("tool") or {}).get("name", "")
        tool_args = (doc.get("policy_target") or {}).get("value")
        if isinstance(resolved, dict):
            user_message = str(resolved.get("user_message") or "")
            artifact_verification = resolved.get("control_artifact_verification") or {}
        else:
            user_message = resolved if isinstance(resolved, str) else str(resolved or "")
            artifact_verification = {}
        try:
            return cc.annotate(
                user_message,
                tool_name,
                tool_args,
                scorer=self.scorer,
                artifact_verification=artifact_verification,
            )
        except Exception:  # noqa: BLE001 - fail closed into the escalate band
            fit = cc.load_fit()
            return {"label": "annotator_error", "score": cc._FAILSAFE_SCORE,
                    "escalate_lo": fit["escalate_lo"], "deny_hi": fit["deny_hi"],
                    "raw": {"error": "annotation_failed"}}

    def _eval(self, ip: str, doc: dict) -> Verdict:  # type: ignore[override]
        annotated = self._annotate(ip, doc)
        tool_name = (doc.get("tool") or {}).get("name", "")
        with _TRACER.start_as_current_span(f"acs_policy.{ip}") as span:
            span.set_attribute("openinference.span.kind", "TOOL")
            span.set_attribute("tool.name", "acs_policy")
            span.set_attribute(
                "input.value",
                json.dumps({
                    "intervention_point": ip,
                    "tool_name": tool_name,
                    "annotations": annotated.get("annotations", {}),
                }, sort_keys=True),
            )
            verdict = super()._eval(ip, annotated)
            span.set_attribute(
                "output.value",
                json.dumps({
                    "decision": verdict.decision,
                    "reason": verdict.reason,
                    "tool_name": tool_name,
                }, sort_keys=True),
            )
            span.set_attribute("acs.intervention_point", ip)
            span.set_attribute("acs.decision", verdict.decision)
            if verdict.reason:
                span.set_attribute("acs.reason", verdict.reason)
        if self.trace and self.trace[-1].get("intervention_point") == ip:
            self.trace[-1]["decision"] = verdict.decision
            self.trace[-1]["reason"] = verdict.reason
        return verdict

    async def run_tool(self, tool_name, args, execute, snapshot=None, mode=None, post_enrich=None):
        """Same as the base shim, except `escalate` at pre_tool_call also blocks.

        The base shim only blocks on `deny` pre-call (it treats escalate as a
        post-call concern). For a learned gate the ambiguous band is the whole
        point: routing to a human approver means the tool does NOT run now, so
        it has to interrupt the call the same way a deny does — the difference
        is the message the caller gets, not whether the action happened.
        """
        snap = snapshot or {}
        pre = self._eval("pre_tool_call", {
            "intervention_point": "pre_tool_call", "snapshot": snap,
            "tool": {"name": tool_name}, "policy_target": {"value": args}})
        if pre.decision in ("deny", "escalate"):
            raise AgentControlBlocked(_Result(pre))
        result = await execute(args)
        post = self._eval("post_tool_call", {
            "intervention_point": "post_tool_call", "snapshot": snap,
            "tool": {"name": tool_name}, "policy_target": {"value": _mcp_text(result)}})
        if post.decision in ("deny", "escalate"):
            raise AgentControlBlocked(_Result(post))
        return _Value(result)
