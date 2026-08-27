"""Native ACS annotator dispatcher for the bank coercion control."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import coercion_classifier as cc


class CoercionAnnotatorDispatcher:
    """Dispatch the manifest's classifier annotator through the host model."""

    def __init__(self, scorer=None) -> None:
        self.scorer = scorer
        self.trace: list[dict] = []

    def dispatch(
        self,
        annotator_name: str,
        annotator_config: Mapping[str, Any],
        preliminary_policy_input: Mapping[str, Any],
    ) -> dict:
        if annotator_name != "coercion_risk":
            raise ValueError(f"unsupported annotator {annotator_name!r}")
        if annotator_config.get("type") != "classifier":
            raise ValueError(
                f"unsupported annotator type {annotator_config.get('type')!r}"
            )
        if (
            annotator_config.get("module") != "coercion_classifier"
            or annotator_config.get("entrypoint") != "annotate"
            or annotator_config.get("calibration")
            != "../runtime/coercion_calibration.json"
        ):
            raise ValueError("coercion annotator manifest does not match host wiring")

        snapshot = preliminary_policy_input.get("snapshot")
        if not isinstance(snapshot, Mapping):
            raise TypeError("native ACS preliminary input is missing snapshot")
        tool = preliminary_policy_input.get("tool")
        policy_target = preliminary_policy_input.get("policy_target")
        if not isinstance(tool, Mapping) or not isinstance(policy_target, Mapping):
            raise TypeError("native ACS preliminary input is missing tool binding")

        tool_name = str(tool.get("name") or "")
        tool_args = policy_target.get("value")
        verification = snapshot.get("control_artifact_verification")
        if not isinstance(verification, Mapping):
            verification = {}

        annotation = cc.annotate(
            str(snapshot.get("user_message") or ""),
            tool_name,
            tool_args,
            scorer=self.scorer,
            artifact_verification=dict(verification),
        )
        self.trace.append(
            {
                "intervention_point": preliminary_policy_input.get(
                    "intervention_point"
                ),
                "tool": tool_name,
                "annotation": annotation,
            }
        )
        return annotation
