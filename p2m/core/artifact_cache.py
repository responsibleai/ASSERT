"""Artifact-level cache and version helpers for suite-scoped outputs.

Cacheable upstream stages (``policy``, ``design``, ``seeds``) write their
outputs into versioned directories under ``<suite>/artifacts/<stage>/v0001``,
``v0002``, ... Each version directory holds its data files alongside a
``artifact.json`` sidecar with a stable input hash. A ``<suite>/latest.json``
pointer records the most recently selected version per stage so that
run-only configs (rollout/judge) can pick up the right inputs without
regenerating upstream artifacts.

Reuse contract:

* On each suite run, ``prepare_artifact_plan`` re-derives an ``input_hash``
  from the relevant inputs (concept text, stage config, upstream artifact
  refs, prompt template files, target config for seeds). If a prior version
  has the same hash and complete outputs, that version is reused; otherwise
  the next ``v####`` directory is allocated.
* ``finalize_artifact_plan`` writes the sidecar, refreshes ``latest.json``,
  and copies primary outputs back to the suite root for legacy readers.
* Run-scoped stages (rollout, judge) consume the activated ref via
  ``ctx["artifact_versions"]`` and never get their own version directory;
  they simply record which seed artifact version they ran against.

Artifact references emitted into manifests/sidecars are always
POSIX-formatted relative-to-suite paths so they read cleanly on every
platform.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from p2m.core.io import PROMPTS_DIR, write_json


log = logging.getLogger(__name__)


CACHEABLE_STAGES = ("policy", "design", "seeds")
ARTIFACTS_DIR = "artifacts"
ARTIFACT_METADATA_FILE = "artifact.json"
LATEST_FILE = "latest.json"

_OUTPUT_FILES: dict[str, dict[str, str]] = {
    "policy": {
        "policy": "policy.json",
        "systematization": "systematization.json",
    },
    "design": {
        "design": "design.json",
    },
    "seeds": {
        "seeds": "seeds.jsonl",
    },
}

_CONTEXT_PATH_KEYS: dict[str, dict[str, str]] = {
    "policy": {
        "policy": "policy_path",
        "systematization": "systematization_path",
    },
    "design": {
        "design": "design_path",
    },
    "seeds": {
        "seeds": "seeds_path",
    },
}

_CONTEXT_DIR_KEYS = {
    "policy": "policy_artifact_dir",
    "design": "design_artifact_dir",
    "seeds": "seeds_artifact_dir",
}

_PROMPT_FILES = {
    "policy": (
        "systematization_single.md",
        "systematization_convert_single.md",
    ),
    "design": ("seeds_design.md",),
    "seeds": (
        "seeds_direct_single.md",
        "seeds_scenario_single.md",
        "seeds_generation_guidance.md",
    ),
}

_OUTPUT_CONFIG_KEYS = {
    "save_dir",
    "save_path",
}


@dataclass(frozen=True)
class ArtifactFingerprint:
    """Stable hash material for a cacheable artifact."""

    stage_name: str
    concept_hash: str | None
    config_hash: str
    input_hash: str
    descriptor: dict[str, Any]


@dataclass(frozen=True)
class ArtifactPlan:
    """A resolved artifact version to either reuse or generate."""

    stage_name: str
    version: str
    artifact_dir: Path
    output_paths: dict[str, Path]
    fingerprint: ArtifactFingerprint
    reused: bool
    metadata: dict[str, Any] | None = None


def is_cacheable_stage(stage_name: str) -> bool:
    return stage_name in CACHEABLE_STAGES


def supports_artifact_cache(ctx: dict[str, Any]) -> bool:
    """Return True when runtime context has enough information for caching."""

    return bool(ctx.get("suite_root") and ctx.get("config_path") and ctx.get("artifacts_root"))


def prepare_artifact_plan(
    *,
    ctx: dict[str, Any],
    stage_name: str,
    raw_cfg: dict[str, Any],
    forced: bool,
) -> ArtifactPlan:
    """Find a matching artifact version or allocate the next version."""

    if stage_name not in CACHEABLE_STAGES:
        raise ValueError(f"unsupported cacheable stage: {stage_name}")
    suite_root = Path(ctx["suite_root"])
    fingerprint = build_artifact_fingerprint(ctx=ctx, stage_name=stage_name, raw_cfg=raw_cfg)
    stage_root = suite_root / ARTIFACTS_DIR / stage_name

    if not forced:
        match = _latest_matching_metadata(stage_name, stage_root, fingerprint.input_hash)
        if match is not None:
            version, metadata = match
            artifact_dir = stage_root / version
            return ArtifactPlan(
                stage_name=stage_name,
                version=version,
                artifact_dir=artifact_dir,
                output_paths=_output_paths(stage_name, artifact_dir),
                fingerprint=fingerprint,
                reused=True,
                metadata=metadata,
            )

    version = _next_version(stage_root)
    artifact_dir = stage_root / version
    return ArtifactPlan(
        stage_name=stage_name,
        version=version,
        artifact_dir=artifact_dir,
        output_paths=_output_paths(stage_name, artifact_dir),
        fingerprint=fingerprint,
        reused=False,
        metadata=None,
    )


def activate_artifact_plan(ctx: dict[str, Any], plan: ArtifactPlan) -> dict[str, Any]:
    """Put selected artifact paths/version metadata into runner context."""

    ctx.setdefault("artifact_versions", {})
    ref = artifact_ref(ctx=ctx, plan=plan, metadata=plan.metadata)
    ctx["artifact_versions"][plan.stage_name] = ref
    ctx[_CONTEXT_DIR_KEYS[plan.stage_name]] = str(plan.artifact_dir)
    for output_key, context_key in _CONTEXT_PATH_KEYS[plan.stage_name].items():
        ctx[context_key] = str(plan.output_paths[output_key])
    return ref


# Per-stage mapping of raw_cfg output-location keys to the canonical artifact
# location they must resolve to when that stage is being cached. Each value is
# either the sentinel ``"__artifact_dir__"`` (meaning "the version directory
# itself") or the name of an entry in ``plan.output_paths``.
_RAW_CFG_OUTPUT_OVERRIDES: dict[str, list[tuple[str, str]]] = {
    "policy": [("save_dir", "__artifact_dir__")],
    "design": [("save_dir", "__artifact_dir__")],
    "seeds": [("save_path", "seeds")],
}


def override_cacheable_output_paths(
    stage_name: str,
    raw_cfg: dict[str, Any],
    plan: ArtifactPlan,
) -> dict[str, Any]:
    """Return a shallow copy of ``raw_cfg`` with cache-managed output keys forced.

    When artifact caching is active for a cacheable stage, the runner must not
    let ``raw_cfg`` redirect outputs (``save_dir`` / ``save_path``) outside the
    versioned artifact directory. ``finalize_artifact_plan`` reads back from
    ``plan.output_paths`` and would otherwise fail (or silently produce stale
    cache entries) if the stage wrote elsewhere.

    Logs a warning per overridden key so users understand why a ``save_dir``
    or ``save_path`` they set in YAML is being ignored — the artifact cache
    owns those paths whenever it is active.
    """

    overrides = _RAW_CFG_OUTPUT_OVERRIDES.get(stage_name)
    if not overrides:
        return raw_cfg
    cfg = dict(raw_cfg)
    for cfg_key, source in overrides:
        if source == "__artifact_dir__":
            new_value = str(plan.artifact_dir)
        else:
            new_value = str(plan.output_paths[source])
        previous = raw_cfg.get(cfg_key)
        if previous is not None and str(previous) != new_value:
            log.warning(
                "[%s] Ignoring %s=%r from config; artifact cache writes outputs "
                "to %s. Use --force-stage %s to regenerate, or disable the cache "
                "for this stage to honor the configured location.",
                stage_name,
                cfg_key,
                previous,
                new_value,
                stage_name,
            )
        cfg[cfg_key] = new_value
    return cfg


def activate_latest_artifacts(ctx: dict[str, Any]) -> None:
    """Load latest artifact refs into context for run-only stage configs.

    When ``latest.json`` references an artifact directory that has been
    deleted, has lost its sidecar, or is missing one of its data files, we
    emit a stderr warning and try to fall back to the most recent valid
    version directory for that stage (if any). A silent skip would let the
    pipeline silently drift to stale legacy compatibility files.
    """

    suite_root = Path(ctx["suite_root"])
    latest = _load_json_object(suite_root / LATEST_FILE)
    artifacts = latest.get("artifacts") if isinstance(latest, dict) else None
    if not isinstance(artifacts, dict):
        return

    for stage_name in CACHEABLE_STAGES:
        ref = artifacts.get(stage_name)
        if not isinstance(ref, dict):
            continue
        version = ref.get("version")
        if not isinstance(version, str) or not version:
            continue
        stage_root = suite_root / ARTIFACTS_DIR / stage_name
        fallback_artifact_dir = stage_root / version
        resolved_artifact_dir = _resolve_ref_path(suite_root, ref.get("artifact_dir"))
        artifact_dir_fallback_used = (
            resolved_artifact_dir is None or not resolved_artifact_dir.exists()
        )
        artifact_dir = (
            fallback_artifact_dir if artifact_dir_fallback_used else resolved_artifact_dir
        )
        resolved_metadata_path = _resolve_ref_path(
            suite_root,
            ref.get("metadata_path") or ref.get("relative_metadata_path"),
        )
        metadata_path_fallback_used = (
            resolved_metadata_path is None or not resolved_metadata_path.exists()
        )
        metadata_path = (
            artifact_dir / ARTIFACT_METADATA_FILE
            if metadata_path_fallback_used
            else resolved_metadata_path
        )
        metadata = _load_json_object(metadata_path)
        if metadata and _metadata_outputs_exist(stage_name, artifact_dir, metadata):
            output_paths = _metadata_output_paths(stage_name, artifact_dir, metadata)
            # If the original ref's path entries pointed at locations that no
            # longer exist, rebuild the ref with the resolved on-disk paths so
            # downstream manifest writes and update_latest don't propagate the
            # stale references any further.
            if artifact_dir_fallback_used or metadata_path_fallback_used:
                ref = _ref_from_metadata(
                    ctx,
                    stage_name=stage_name,
                    version=version,
                    artifact_dir=artifact_dir,
                    metadata=metadata,
                    primary_path=output_paths[next(iter(_OUTPUT_FILES[stage_name]))],
                )
                update_latest(ctx, stage_name, ref)
                print(
                    f"[artifact-cache] warning: latest.json {stage_name} entry "
                    f"referenced missing paths; rebuilt ref pointing at the "
                    f"current on-disk location of version {version}.",
                    file=sys.stderr,
                )
            ctx.setdefault("artifact_versions", {})[stage_name] = ref
            ctx[_CONTEXT_DIR_KEYS[stage_name]] = str(artifact_dir)
            for output_key, context_key in _CONTEXT_PATH_KEYS[stage_name].items():
                if output_key in output_paths:
                    ctx[context_key] = str(output_paths[output_key])
            refresh_compatibility_files(ctx, stage_name, output_paths)
            continue

        recovery = _recover_latest_valid_version(stage_name, stage_root)
        if recovery is None:
            print(
                f"[artifact-cache] warning: latest.json references missing or "
                f"incomplete {stage_name} artifact {version}; no valid prior "
                f"version was found.",
                file=sys.stderr,
            )
            continue
        recovered_version, recovered_dir, recovered_metadata = recovery
        recovered_outputs = _metadata_output_paths(
            stage_name, recovered_dir, recovered_metadata
        )
        recovered_ref = _ref_from_metadata(
            ctx,
            stage_name=stage_name,
            version=recovered_version,
            artifact_dir=recovered_dir,
            metadata=recovered_metadata,
            primary_path=recovered_outputs[
                next(iter(_OUTPUT_FILES[stage_name]))
            ],
        )
        ctx.setdefault("artifact_versions", {})[stage_name] = recovered_ref
        ctx[_CONTEXT_DIR_KEYS[stage_name]] = str(recovered_dir)
        for output_key, context_key in _CONTEXT_PATH_KEYS[stage_name].items():
            if output_key in recovered_outputs:
                ctx[context_key] = str(recovered_outputs[output_key])
        refresh_compatibility_files(ctx, stage_name, recovered_outputs)
        update_latest(ctx, stage_name, recovered_ref)
        print(
            f"[artifact-cache] warning: latest.json {stage_name} entry was "
            f"missing or incomplete; recovered to version {recovered_version}.",
            file=sys.stderr,
        )


def finalize_artifact_plan(ctx: dict[str, Any], plan: ArtifactPlan) -> dict[str, Any]:
    """Write sidecar metadata and update latest/compatibility artifacts."""

    plan.artifact_dir.mkdir(parents=True, exist_ok=True)
    file_hashes = _file_hashes(plan.output_paths)
    hashes: dict[str, Any] = {
        "config_hash": plan.fingerprint.config_hash,
        "input_hash": plan.fingerprint.input_hash,
    }
    if plan.fingerprint.concept_hash is not None:
        hashes["concept_hash"] = plan.fingerprint.concept_hash
    metadata = {
        "schema_version": 1,
        "artifact_type": plan.stage_name,
        "version": plan.version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "hashes": hashes,
        "inputs": plan.fingerprint.descriptor,
        "files": {
            key: path.name for key, path in plan.output_paths.items()
        },
        "file_hashes": file_hashes,
    }
    write_json(plan.artifact_dir / ARTIFACT_METADATA_FILE, metadata)
    ref = artifact_ref(ctx=ctx, plan=plan, metadata=metadata)
    ctx.setdefault("artifact_versions", {})[plan.stage_name] = ref
    update_latest(ctx, plan.stage_name, ref)
    refresh_compatibility_files(ctx, plan.stage_name, plan.output_paths)
    return ref


def discard_artifact_plan(ctx: dict[str, Any], plan: ArtifactPlan) -> None:
    """Roll back an allocated artifact version after a stage failure.

    Removes the (possibly partially-written) version directory and clears the
    stage's entry from ``ctx['artifact_versions']`` so that:

    * no dead ``vNNNN/`` directory accumulates between failures (otherwise
      ``_next_version`` keeps incrementing past abandoned slots and the
      stage_root fills with empty/incomplete version dirs over time), and
    * the failed run's manifest does not record an ``artifact_versions``
      reference pointing at a directory we just deleted.

    Reused plans are left untouched: the on-disk artifact predates this run,
    and a downstream stage failure must not blow away a healthy cached
    upstream artifact whose sidecar is intact. ``latest.json`` is also left
    alone — ``finalize_artifact_plan`` is the only writer for non-reused
    plans, so a non-reused plan that reaches ``discard_artifact_plan`` never
    updated ``latest.json`` in the first place.
    """

    if plan.reused:
        return
    artifact_dir = plan.artifact_dir
    if artifact_dir.exists() and artifact_dir.is_dir():
        try:
            shutil.rmtree(artifact_dir)
        except OSError as exc:
            log.warning(
                "[artifact-cache] failed to clean up abandoned %s version %s "
                "at %s: %s",
                plan.stage_name,
                plan.version,
                artifact_dir,
                exc,
            )
            return
    artifacts = ctx.get("artifact_versions")
    if isinstance(artifacts, dict):
        artifacts.pop(plan.stage_name, None)


def refresh_compatibility_files(
    ctx: dict[str, Any],
    stage_name: str,
    output_paths: dict[str, Path],
) -> None:
    """Copy selected version outputs back to legacy suite-root filenames."""

    suite_root = Path(ctx["suite_root"])
    for path in output_paths.values():
        if path.exists():
            shutil.copy2(path, suite_root / path.name)


def update_latest(ctx: dict[str, Any], stage_name: str, ref: dict[str, Any]) -> None:
    suite_root = Path(ctx["suite_root"])
    latest_path = suite_root / LATEST_FILE
    latest = _load_json_object(latest_path) or {"schema_version": 1, "artifacts": {}}
    artifacts = latest.setdefault("artifacts", {})
    if not isinstance(artifacts, dict):
        artifacts = {}
        latest["artifacts"] = artifacts
    artifacts[stage_name] = ref
    write_json(latest_path, latest)


def artifact_ref(
    *,
    ctx: dict[str, Any],
    plan: ArtifactPlan,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the compact artifact reference stored in manifests/context."""

    suite_root = Path(ctx["suite_root"])
    primary_key = next(iter(_OUTPUT_FILES[plan.stage_name]))
    primary_path = plan.output_paths[primary_key]
    sidecar_path = plan.artifact_dir / ARTIFACT_METADATA_FILE
    relative_path = _relative_to_suite(primary_path, suite_root)
    relative_artifact_dir = _relative_to_suite(plan.artifact_dir, suite_root)
    relative_metadata_path = _relative_to_suite(sidecar_path, suite_root)
    hashes = metadata.get("hashes", {}) if isinstance(metadata, dict) else {}
    file_hashes = metadata.get("file_hashes", {}) if isinstance(metadata, dict) else {}
    ref: dict[str, Any] = {
        "artifact_type": plan.stage_name,
        "version": plan.version,
        "input_hash": hashes.get("input_hash", plan.fingerprint.input_hash),
        "config_hash": hashes.get("config_hash", plan.fingerprint.config_hash),
        "path": relative_path,
        "artifact_dir": relative_artifact_dir,
        "metadata_path": relative_metadata_path,
        "file_hashes": file_hashes,
    }
    concept_hash = hashes.get("concept_hash", plan.fingerprint.concept_hash)
    if concept_hash is not None:
        ref["concept_hash"] = concept_hash
    return ref


def _ref_from_metadata(
    ctx: dict[str, Any],
    *,
    stage_name: str,
    version: str,
    artifact_dir: Path,
    metadata: dict[str, Any],
    primary_path: Path,
) -> dict[str, Any]:
    """Build a ref payload from on-disk metadata (no plan/fingerprint needed)."""

    suite_root = Path(ctx["suite_root"])
    sidecar_path = artifact_dir / ARTIFACT_METADATA_FILE
    hashes = metadata.get("hashes", {}) if isinstance(metadata, dict) else {}
    file_hashes = metadata.get("file_hashes", {}) if isinstance(metadata, dict) else {}
    ref: dict[str, Any] = {
        "artifact_type": stage_name,
        "version": version,
        "input_hash": hashes.get("input_hash"),
        "config_hash": hashes.get("config_hash"),
        "path": _relative_to_suite(primary_path, suite_root),
        "artifact_dir": _relative_to_suite(artifact_dir, suite_root),
        "metadata_path": _relative_to_suite(sidecar_path, suite_root),
        "file_hashes": file_hashes,
    }
    concept_hash = hashes.get("concept_hash")
    if concept_hash is not None:
        ref["concept_hash"] = concept_hash
    return ref


def build_artifact_fingerprint(
    *,
    ctx: dict[str, Any],
    stage_name: str,
    raw_cfg: dict[str, Any],
) -> ArtifactFingerprint:
    descriptor = _stage_descriptor(ctx=ctx, stage_name=stage_name, raw_cfg=raw_cfg)
    concept_hash = descriptor.get("concept_hash")
    config_hash = hash_payload(descriptor["config"])
    input_hash = hash_payload({
        "stage_name": stage_name,
        "concept_hash": concept_hash,
        "config_hash": config_hash,
        "dependencies": descriptor.get("dependencies", {}),
        "prompts": descriptor.get("prompts", {}),
    })
    return ArtifactFingerprint(
        stage_name=stage_name,
        concept_hash=concept_hash if isinstance(concept_hash, str) else None,
        config_hash=config_hash,
        input_hash=input_hash,
        descriptor=descriptor,
    )


def hash_payload(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stage_descriptor(
    *,
    ctx: dict[str, Any],
    stage_name: str,
    raw_cfg: dict[str, Any],
) -> dict[str, Any]:
    # ``concept_hash`` is computed only for the policy stage. Downstream
    # cacheable stages (design, seeds) do NOT recompute it — they pick the
    # concept change up transitively via ``_dependency_descriptor``: design
    # depends on policy's input_hash and seeds depends on design's, so any
    # change to the concept invalidates policy's input_hash and the cascade
    # invalidates every stage that consumes it. This relies on the
    # dependency chain being complete; if a future cacheable stage stops
    # depending on its upstream artifact, hash this concept directly here
    # too or the cache will reuse stale outputs after a concept edit.
    concept_hash = None
    if stage_name == "policy":
        concept_hash = hash_payload({
            "concept_name": ctx.get("concept_name"),
            "concept": ctx.get("concept"),
        })

    descriptor: dict[str, Any] = {
        "stage_name": stage_name,
        "config": _stage_config_descriptor(ctx=ctx, stage_name=stage_name, raw_cfg=raw_cfg),
        "dependencies": _dependency_descriptor(ctx=ctx, stage_name=stage_name),
        "prompts": _prompt_descriptor(stage_name),
    }
    if concept_hash is not None:
        descriptor["concept_hash"] = concept_hash
    return descriptor


def _stage_config_descriptor(
    *,
    ctx: dict[str, Any],
    stage_name: str,
    raw_cfg: dict[str, Any],
) -> dict[str, Any]:
    cfg = {
        key: value
        for key, value in raw_cfg.items()
        if key not in _OUTPUT_CONFIG_KEYS
    }
    descriptor: dict[str, Any] = {
        "stage_config": cfg,
    }
    if stage_name in {"policy", "design", "seeds"}:
        descriptor["context"] = ctx.get("context")
    if stage_name in {"design", "seeds"}:
        descriptor["factors"] = ctx.get("factors")
    if stage_name == "seeds":
        descriptor["target"] = _normalize_value(ctx.get("target"))
    return descriptor


def _dependency_descriptor(ctx: dict[str, Any], stage_name: str) -> dict[str, Any]:
    if stage_name == "policy":
        return {}
    deps: dict[str, Any] = {}
    if stage_name in {"design", "seeds"}:
        deps["policy"] = _artifact_or_file_dependency(ctx, "policy", "policy_path")
    if stage_name == "seeds":
        deps["design"] = _artifact_or_file_dependency(ctx, "design", "design_path")
    return deps


def _artifact_or_file_dependency(
    ctx: dict[str, Any],
    artifact_type: str,
    context_path_key: str,
) -> dict[str, Any] | None:
    ref = (ctx.get("artifact_versions") or {}).get(artifact_type)
    if isinstance(ref, dict):
        return {
            "artifact_type": ref.get("artifact_type", artifact_type),
            "version": ref.get("version"),
            "input_hash": ref.get("input_hash"),
            "path": ref.get("path") or ref.get("relative_path"),
        }
    raw_path = ctx.get(context_path_key)
    if not raw_path and ctx.get("suite_root"):
        default_name = {
            "policy_path": "policy.json",
            "design_path": "design.json",
        }.get(context_path_key)
        if default_name:
            raw_path = str(Path(ctx["suite_root"]) / default_name)
    if isinstance(raw_path, str) and raw_path:
        path = Path(raw_path)
        if path.exists():
            return {
                "path": str(path),
                "sha256": file_sha256(path),
            }
    return None


def _prompt_descriptor(stage_name: str) -> dict[str, str]:
    prompts: dict[str, str] = {}
    for filename in _PROMPT_FILES.get(stage_name, ()):
        path = PROMPTS_DIR / filename
        prompts[filename] = file_sha256(path) if path.exists() else ""
    return prompts


def _canonical_json(payload: Any) -> str:
    return json.dumps(_normalize_value(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalize_value(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _normalize_value(dataclasses.asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _normalize_value(val)
            for key, val in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    if isinstance(value, set):
        return sorted(_normalize_value(item) for item in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _latest_matching_metadata(
    stage_name: str, stage_root: Path, input_hash: str
) -> tuple[str, dict[str, Any]] | None:
    matches: list[tuple[str, dict[str, Any]]] = []
    for version_dir in _iter_version_dirs(stage_root):
        metadata = _load_json_object(version_dir / ARTIFACT_METADATA_FILE)
        if not metadata:
            continue
        hashes = metadata.get("hashes")
        if isinstance(hashes, dict) and hashes.get("input_hash") == input_hash:
            if _metadata_outputs_exist(stage_name, version_dir, metadata):
                matches.append((version_dir.name, metadata))
    return matches[-1] if matches else None


def _recover_latest_valid_version(
    stage_name: str,
    stage_root: Path,
) -> tuple[str, Path, dict[str, Any]] | None:
    """Return the most recent intact version dir for a stage, if any."""

    for version_dir in reversed(_iter_version_dirs(stage_root)):
        metadata = _load_json_object(version_dir / ARTIFACT_METADATA_FILE)
        if metadata and _metadata_outputs_exist(stage_name, version_dir, metadata):
            return version_dir.name, version_dir, metadata
    return None


def _is_safe_artifact_basename(filename: Any) -> bool:
    if not isinstance(filename, str) or not filename or filename in {".", ".."}:
        return False
    candidate = Path(filename)
    if candidate.is_absolute():
        return False
    if candidate.name != filename:
        return False
    if any(sep in filename for sep in (os.sep, os.altsep) if sep):
        return False
    return True


def _metadata_outputs_exist(
    stage_name: str, version_dir: Path, metadata: dict[str, Any]
) -> bool:
    """Return True iff every expected output file for ``stage_name`` exists.

    Uses the merged path map from :func:`_metadata_output_paths` so a partial
    or missing ``metadata['files']`` cannot trick the cache into activating a
    half-written artifact (or one that is missing the primary output file).
    """

    output_paths = _metadata_output_paths(stage_name, version_dir, metadata)
    expected_keys = _OUTPUT_FILES.get(stage_name, {}).keys()
    if not expected_keys:
        return False
    for key in expected_keys:
        path = output_paths.get(key)
        if path is None or not path.exists():
            return False
    return True


def _next_version(stage_root: Path) -> str:
    numbers = []
    for version_dir in _iter_version_dirs(stage_root):
        match = re.fullmatch(r"v(\d{4})", version_dir.name)
        if match:
            numbers.append(int(match.group(1)))
    return f"v{(max(numbers) + 1) if numbers else 1:04d}"


def _iter_version_dirs(stage_root: Path) -> list[Path]:
    if not stage_root.exists():
        return []
    return sorted(
        [path for path in stage_root.iterdir() if path.is_dir() and re.fullmatch(r"v\d{4}", path.name)],
        key=lambda path: path.name,
    )


def _resolve_ref_path(suite_root: Path, raw_path: Any) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path:
        return None
    path = Path(raw_path)
    suite_root_resolved = suite_root.resolve()
    if path.is_absolute():
        resolved_path = path.resolve()
        try:
            resolved_path.relative_to(suite_root_resolved)
        except ValueError:
            print(
                f"[artifact-cache] warning: refusing to resolve absolute cache reference "
                f"outside suite root: {raw_path!r}",
                file=sys.stderr,
            )
            return None
        return resolved_path
    parts = [part for part in raw_path.replace("\\", "/").split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        # Defense in depth: a tampered or corrupted latest.json must not be
        # able to point activate_latest_artifacts at a location outside the
        # suite root.
        print(
            f"[artifact-cache] warning: refusing to resolve cache reference "
            f"with parent-directory segments: {raw_path!r}",
            file=sys.stderr,
        )
        return None
    return suite_root.joinpath(*parts)


def _relative_to_suite(path: Path, suite_root: Path) -> str:
    try:
        return path.resolve().relative_to(suite_root.resolve()).as_posix()
    except ValueError:
        # Path lives outside suite_root. Prefer a relative path when possible,
        # but on Windows os.path.relpath raises ValueError for different drives.
        # Fall back to a resolved absolute POSIX path so manifest/sidecar
        # generation remains robust across platforms.
        try:
            return Path(os.path.relpath(path, suite_root)).as_posix()
        except ValueError:
            return path.resolve().as_posix()


def _output_paths(stage_name: str, artifact_dir: Path) -> dict[str, Path]:
    return {
        key: artifact_dir / filename
        for key, filename in _OUTPUT_FILES[stage_name].items()
    }


def _metadata_output_paths(
    stage_name: str,
    artifact_dir: Path,
    metadata: dict[str, Any],
) -> dict[str, Path]:
    """Return on-disk paths for every expected output of ``stage_name``.

    Always populates the full keyset from :data:`_OUTPUT_FILES` so callers can
    safely index ``output_paths[<primary_key>]`` even when a partial or
    legacy ``metadata['files']`` is missing entries. Filenames provided by
    metadata override the canonical defaults so artifacts written under
    older schemas continue to resolve correctly.
    """

    paths: dict[str, Path] = dict(_output_paths(stage_name, artifact_dir))
    files = metadata.get("files")
    if isinstance(files, dict):
        for key, filename in files.items():
            if not isinstance(key, str):
                continue
            if not _is_safe_artifact_basename(filename):
                # Skip entries that would let a tampered artifact.json point at
                # files outside the version directory (absolute paths, parent
                # segments, embedded separators). The canonical default from
                # ``_output_paths`` remains in place for this key.
                continue
            paths[key] = artifact_dir / filename
    return paths


def _file_hashes(output_paths: dict[str, Path]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for key, path in output_paths.items():
        if not path.exists():
            raise FileNotFoundError(f"expected artifact output missing: {path}")
        hashes[key] = file_sha256(path)
    return hashes


def _load_json_object(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        print(
            f"[artifact-cache] warning: failed to read {path}: {exc}",
            file=sys.stderr,
        )
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        print(
            f"[artifact-cache] warning: ignoring corrupt JSON at {path}: {exc}",
            file=sys.stderr,
        )
        return None
    if not isinstance(payload, dict):
        return None
    return payload
