"""Native ACS annotator dispatcher for the bank coercion control."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import bank_core
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
        user_message, within_bound = cc._normalized_user_message(
            str(snapshot.get("user_message") or "")
        )
        tool = preliminary_policy_input.get("tool")
        policy_target = preliminary_policy_input.get("policy_target")
        if not isinstance(tool, Mapping) or not isinstance(policy_target, Mapping):
            raise TypeError("native ACS preliminary input is missing tool binding")

        tool_name = str(tool.get("name") or "")
        tool_args = policy_target.get("value")
        args = dict(tool_args) if isinstance(tool_args, Mapping) else {}
        session_id = snapshot.get("control_session_id")
        action_context = snapshot.get("current_action_binding")
        binding_seal = snapshot.get("current_action_binding_seal")
        try:
            binding = (
                bank_core._canonical_binding_value(dict(action_context))
                if isinstance(action_context, Mapping)
                else None
            )
        except (TypeError, ValueError):
            binding = None
        if (
            not isinstance(session_id, str)
            or not session_id
            or not isinstance(binding, dict)
            or not isinstance(binding_seal, str)
            or not bank_core._validate_control_action_binding(
                binding,
                binding_seal,
                user_message,
                tool_name,
                args,
                session_id,
            )
        ):
            verification = cc._verification_failure(
                cc.ARTIFACT_VERIFICATION_BINDING_MISMATCH,
                tool_name,
                args,
            )
        else:
            verification = bank_core.verify_control_artifacts(
                user_message,
                tool_name,
                args,
                session_id,
                current_action_context=binding,
            )
        if not within_bound:
            verification = cc._verification_failure(
                bank_core.CONTROL_REFERENCE_INPUT_TOO_LONG,
                tool_name,
                args,
                verification,
            )

        annotation = cc._annotate_trusted(
            user_message,
            tool_name,
            args,
            scorer=self.scorer,
            artifact_verification=verification,
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
