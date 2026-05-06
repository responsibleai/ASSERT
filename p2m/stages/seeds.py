"""Generate prompt and scenario seeds for the unified pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

from p2m.config import parse_model_config, reject_unknown_keys, resolve_stage_paths
from p2m.core.async_utils import gather_limited
from p2m.core.config_model import (
    DEFAULT_GENERATION_MAX_TOKENS,
    DEFAULT_GENERATION_TEMPERATURE,
    TargetConfig,
)
from p2m.core.io import (
    design_factors,
    fill_template,
    load_policy,
    load_prompt_text,
    normalize_seed_context,
    normalize_seed_rows,
    resolve_path,
    slugify,
    write_jsonl,
)
from p2m.core.model_client import GenerateOptions, generate_structured
from p2m.core.tools import normalize_tool_defs
from p2m.stages.design import normalize_design

BASE_DIR = Path(__file__).resolve().parents[2]
PROMPT_SEED_TEMPLATE = (BASE_DIR / "prompts" / "seeds_direct_single.md").read_text(encoding="utf-8")
SCENARIO_SEED_TEMPLATE = (BASE_DIR / "prompts" / "seeds_scenario_single.md").read_text(encoding="utf-8")
SEEDS_FILE = "seeds.jsonl"
SCOPE = "suite"
SUITE_OUTPUT = SEEDS_FILE

TOOL_SOURCE_RUNTIME = "runtime"
TOOL_SOURCE_PER_SEED = "per_seed"

SEED_ID_PREFIX = {"prompt": "ps", "scenario": "as"}
GENERATION_GUIDANCE_TEMPLATE = load_prompt_text("seeds_generation_guidance.md")
# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

PROMPT_FIELD_EXAMPLES: dict[str, str] = {
    "title": "Short descriptive title",
    "description": "A complete standalone user message.",
    "system_prompt": "Explicit system prompt for this seed, or empty string if not needed",
}

SCENARIO_FIELD_EXAMPLES: dict[str, str] = {
    "title": "Short phrase naming the situation",
    "description": "One flowing second-person paragraph",
    "system_prompt": "Explicit system prompt for this seed, or empty string if not needed",
}

FIELD_EXAMPLES = {"prompt": PROMPT_FIELD_EXAMPLES, "scenario": SCENARIO_FIELD_EXAMPLES}

TOOL_FIELD_EXAMPLES: dict[str, Any] = {
    "name": "tool_name",
    "description": "What the tool does",
    "parameters": [{"name": "param_name", "type": "string", "description": "What this parameter is"}],
}


def output_schema_example(
    kind: str,
    tool_source: str = TOOL_SOURCE_RUNTIME,
) -> str:
    """Build the JSON output-schema example for seed generation prompts."""
    schema = SEED_SCHEMA_WITH_TOOLS if tool_source == TOOL_SOURCE_PER_SEED else SEED_SCHEMA
    field_examples = FIELD_EXAMPLES[kind]
    seed: dict[str, Any] = {}
    for prop_name in schema.get("properties", {}):
        if prop_name in field_examples:
            seed[prop_name] = field_examples[prop_name]
        elif prop_name == "tools":
            seed["tools"] = [TOOL_FIELD_EXAMPLES]
    return json.dumps({"seeds": [seed]}, indent=2)


def _validate_tool_source(tool_source: str, target: TargetConfig | None) -> None:
    if tool_source not in {TOOL_SOURCE_RUNTIME, TOOL_SOURCE_PER_SEED}:
        raise ValueError("seeds.tool_source must be 'runtime' or 'per_seed'")
    if tool_source == TOOL_SOURCE_RUNTIME:
        return
    if target is None or not target.model:
        raise ValueError("seeds.tool_source=per_seed requires target.model")
    if target.connector:
        raise ValueError("seeds.tool_source=per_seed does not support target.connector")
    if target.tools is None or not target.tools.simulator:
        raise ValueError("seeds.tool_source=per_seed requires target.tools.simulator")
    if target.tools.module:
        raise ValueError("seeds.tool_source=per_seed does not support target.tools.module")
    if target.tools.toolset:
        raise ValueError("seeds.tool_source=per_seed does not support target.tools.toolset")


# ---------------------------------------------------------------------------
# Schema & record builder
# ---------------------------------------------------------------------------

SEED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "system_prompt": {"type": "string"},
    },
    "required": ["title", "description", "system_prompt"],
}

SEED_SCHEMA_WITH_TOOLS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "system_prompt": {"type": "string"},
        "tools": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "parameters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "name": {"type": "string"},
                                "type": {"type": "string"},
                                "description": {"type": "string"},
                            },
                            "required": ["name", "type", "description"],
                        },
                    },
                },
                "required": ["name", "description", "parameters"],
            },
        },
    },
    "required": ["title", "description", "system_prompt", "tools"],
}


def seeds_response_schema(tool_source: str = TOOL_SOURCE_RUNTIME) -> dict[str, Any]:
    """Full response schema wrapping seeds in a ``{"seeds": [...]}`` envelope."""
    item_schema = SEED_SCHEMA_WITH_TOOLS if tool_source == TOOL_SOURCE_PER_SEED else SEED_SCHEMA
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "seeds": {"type": "array", "maxItems": 2000, "items": item_schema},
        },
        "required": ["seeds"],
    }


def normalize_generated_seed(
    raw_seed: dict[str, Any],
    *,
    tool_source: str,
    fixed_system_prompt: str | None,
) -> dict[str, Any]:
    payload = {
        "title": str(raw_seed.get("title") or ""),
        "description": str(raw_seed.get("description") or ""),
    }
    if not payload["description"].strip():
        raise ValueError("generated seed is missing description")

    system_prompt = str(raw_seed.get("system_prompt") or "").strip()
    if system_prompt and fixed_system_prompt is None:
        payload["system_prompt"] = system_prompt

    raw_tools = raw_seed.get("tools")
    if tool_source == TOOL_SOURCE_PER_SEED:
        if not isinstance(raw_tools, list) or not raw_tools:
            raise ValueError("generated seed requires non-empty tools when seeds.tool_source=per_seed")
        try:
            normalize_tool_defs(raw_tools)
        except (KeyError, TypeError) as exc:
            raise ValueError("generated seed contains invalid tool definitions") from exc
        payload["tools"] = raw_tools
    elif raw_tools:
        raise ValueError("generated seed.tools is only allowed when seeds.tool_source=per_seed")

    return payload


def seed_record(
    *,
    kind: str,
    seed_id: str,
    concept: str,
    seed_payload: dict[str, Any],
    factors: dict[str, str] | None = None,
) -> dict[str, Any]:
    record = {
        "kind": kind,
        "seed_id": seed_id,
        "concept": concept,
        "seed": seed_payload,
    }
    if factors:
        record["factors"] = factors
    return record


# ---------------------------------------------------------------------------
# Template replacement builders
# ---------------------------------------------------------------------------

_TOOL_INSTRUCTIONS = {
    TOOL_SOURCE_RUNTIME: """
## Target affordances (runtime-backed agentic modality)

This is an **agentic** audit, but the target's actual tools come from the runtime target binding.
Do **not** define tool schemas in the output.

Instead, use the target profile below to shape realistic scenarios:
- What kinds of actions the target can plausibly take
- Which action families matter for this risk
- What boundary conditions are worth probing
""",
    TOOL_SOURCE_PER_SEED: """
## Tools (agentic modality)

This is an **agentic** audit. The target model will have access to tools during the conversation.
For each seed, define 1-5 realistic tools that the target model would have in this scenario.
""",
}


def _render_policy_body(policy: dict[str, Any]) -> str:
    return json.dumps(policy, indent=2, ensure_ascii=False)


def _template_replacements(
    kind: str,
    concept_name: str,
    behavior_name: str,
    behavior_description: str,
    examples: list[str],
    count: int,
    *,
    context: str | None,
    batch_guidance: str,
    policy_body: str,
    tool_source: str = TOOL_SOURCE_RUNTIME,
) -> dict[str, str]:
    return {
        "concept": concept_name,
        "behavior": behavior_name,
        "definition": behavior_description,
        "count": str(count),
        "context": context or "",
        "examples": (
            "\n".join(f"- {e}" for e in examples)
            if examples
            else "- (no examples provided)"
        ),
        "policy_body": policy_body,
        "tool_instructions": _TOOL_INSTRUCTIONS.get(tool_source, ""),
        "output_schema": output_schema_example(kind, tool_source),
        "batch_guidance": batch_guidance,
    }


def render_tuple_spec(
    tuple_spec: dict[str, dict[str, Any]],
    *,
    include_behavior: bool = True,
) -> str:
    if not tuple_spec:
        return ""
    axes = [ax for ax in tuple_spec if ax != "behavior"]
    lines = []
    for ax in axes:
        title = ax.replace("_", " ").capitalize()
        lines.append(
            f"- {title}: {tuple_spec[ax]['name']} - {tuple_spec[ax]['definition']}"
        )
    if include_behavior and "behavior" in tuple_spec:
        lines.append(
            f"- Policy behavior: {tuple_spec['behavior']['name']} — {tuple_spec['behavior']['description']}"
        )
    return "\n".join(lines)


def build_generation_prompt(
    *,
    kind: str,
    policy: dict[str, Any],
    behavior: dict[str, Any],
    count: int,
    context: str | None,
    design: dict[str, list[dict[str, Any]]],
    tuple_spec: dict[str, dict[str, Any]] | None = None,
    tool_source: str = TOOL_SOURCE_RUNTIME,
) -> str:
    """Build the full generation prompt for one tuple's batch.

    *kind*: ``"prompt"`` or ``"scenario"``.
    """
    concept_name = str(policy.get("concept", {}).get("name") or "concept")
    behavior_name = str(behavior.get("name") or "").strip()
    behavior_description = str(behavior.get("description") or "").strip()
    policy_behavior = next(
        (
            entry
            for entry in policy.get("behaviors", [])
            if str(entry.get("name") or "").strip() == behavior_name
        ),
        None,
    )
    behavior_examples = (
        list(policy_behavior.get("examples") or [])
        if isinstance(policy_behavior, dict)
        else []
    )

    if not tuple_spec:
        raise ValueError("generation requires tuple_spec")
    include_pn = (
        "behavior" in tuple_spec
        and (kind == "scenario" or not design_factors(design))
    )
    batch_guidance = fill_template(
        GENERATION_GUIDANCE_TEMPLATE,
        {
                "count": str(count),
                "behavior_name": behavior_name,
                "behavior_definition": behavior_description,
                "tuple_block": render_tuple_spec(
                    tuple_spec, include_behavior=include_pn
                ),
        },
    )

    replacements = _template_replacements(
        kind,
        concept_name,
        behavior_name,
        behavior_description,
        behavior_examples,
        count,
        context=context,
        batch_guidance=batch_guidance,
        policy_body=_render_policy_body(policy),
        tool_source=tool_source,
    )
    return fill_template(
        PROMPT_SEED_TEMPLATE if kind == "prompt" else SCENARIO_SEED_TEMPLATE,
        replacements,
    )


# ---------------------------------------------------------------------------
# Covering array construction
# ---------------------------------------------------------------------------

def build_covering_array(
    design: dict[str, list[dict[str, str]]],
    rng: random.Random,
    *,
    axes: tuple[str, ...] | None = None,
) -> list[dict[str, str]]:
    """Build a strength-2 covering array for the design factors.

    Returns a set of tuples such that every pair of factors has all
    level-combinations represented at least once.  Each tuple is a
    flat dict mapping factor name to level name.

    Uses IPOG (In-Parameter-Order-General) for construction.
    """
    active_axes = design_factors(design) if axes is None else tuple(axes)
    if not active_axes:
        raise ValueError("at least one factor is required")

    cardinalities = [len(design[ax]) for ax in active_axes]
    if any(c == 0 for c in cardinalities):
        raise ValueError("all factors must have at least one level")

    if len(active_axes) < 2:
        return [{active_axes[0]: entry["name"]} for entry in design[active_axes[0]]]

    return _ipog(design, active_axes, rng)


def _ipog(
    design: dict[str, list[dict[str, str]]],
    active_axes: tuple[str, ...],
    rng: random.Random,
) -> list[dict[str, str]]:
    """IPOG (In-Parameter-Order-General) strength-2 covering array.

    Builds the array column-by-column: starts with a full cross of the
    first two factors, then extends one factor at a time. For each new
    factor, horizontal extension greedily assigns levels to existing
    rows to maximize pair coverage; vertical extension adds rows for any
    remaining uncovered pairs.
    """
    k = len(active_axes)
    levels = [[entry["name"] for entry in design[ax]] for ax in active_axes]

    # Full cross of first two factors
    rows: list[list[str | None]] = [
        [a, b] + [None] * (k - 2)
        for a in levels[0]
        for b in levels[1]
    ]

    for col in range(2, k):
        col_levels = levels[col]

        # Uncovered pairs between new column and each prior column
        uncovered: set[tuple[int, str, str]] = {
            (prior, pl, nl)
            for prior in range(col)
            for pl in levels[prior]
            for nl in col_levels
        }

        # Horizontal extension: assign best level for new column in each row
        for row in rows:
            best_levels: list[str] = []
            best_count = -1
            for nl in col_levels:
                count = sum(
                    1 for prior in range(col)
                    if (prior, row[prior], nl) in uncovered
                )
                if count > best_count:
                    best_count = count
                    best_levels = [nl]
                elif count == best_count:
                    best_levels.append(nl)
            chosen = rng.choice(best_levels)
            row[col] = chosen
            for prior in range(col):
                uncovered.discard((prior, row[prior], chosen))

        # Vertical extension: add rows for remaining uncovered pairs
        new_rows: list[list[str | None]] = []
        for prior, pl, nl in sorted(uncovered):
            merged = False
            for nr in new_rows:
                if nr[col] != nl:
                    continue
                if nr[prior] is not None and nr[prior] != pl:
                    continue
                nr[prior] = pl
                merged = True
                break
            if not merged:
                nr = [None] * k
                nr[col] = nl
                nr[prior] = pl
                new_rows.append(nr)

        for nr in new_rows:
            for i in range(col + 1):
                if nr[i] is None:
                    nr[i] = rng.choice(levels[i])
            rows.append(nr)

    return [
        {active_axes[i]: row[i] for i in range(k)}  # type: ignore[misc]
        for row in rows
    ]


def sample_from_covering_array(
    covering_array: list[dict[str, str]],
    n: int,
    rng: random.Random,
) -> list[dict[str, str]]:
    """Draw *n* tuples from a precomputed covering array.

    If *n* <= array size, shuffle and take the first *n*.
    If *n* > array size, use the full array then cycle with reshuffling.
    """
    if n <= 0:
        raise ValueError("sample size must be > 0")
    if not covering_array:
        raise ValueError("covering array is empty")

    if n <= len(covering_array):
        pool = list(covering_array)
        rng.shuffle(pool)
        return pool[:n]

    result: list[dict[str, str]] = []
    while len(result) < n:
        batch = list(covering_array)
        rng.shuffle(batch)
        result.extend(batch[: n - len(result)])
    return result


# ---------------------------------------------------------------------------
# Job building
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedJob:
    """One covering-array tuple's seed-generation work unit."""
    order: int
    behavior: dict[str, Any]
    count: int
    start_index: int
    tuple_spec: dict[str, dict[str, Any]] | None = None


def build_generation_jobs(
    *,
    policy: dict[str, Any],
    design: dict[str, list[dict[str, Any]]],
    sample_size: int,
    rng: random.Random,
) -> tuple[list[SeedJob], list[dict[str, str]] | None]:
    """Build generation jobs — one per covering-array tuple.

    Budget is spread evenly across tuples via divmod. Each job produces
    its allocated number of seeds for a single (behavior, factor…) tuple.
    """
    behavior_entries = design["behavior"]
    factor_names = design_factors(design)
    level_lookup: dict[str, dict[str, dict[str, str]]] = {
        factor_name: {
            entry["name"]: entry for entry in design[factor_name]
        }
        for factor_name in factor_names
    }
    behavior_level_lookup = {
        str(entry["name"]): entry for entry in behavior_entries
    }
    covering_design = dict(design)
    covering_design["behavior"] = behavior_entries
    all_axes = ("behavior",) + factor_names if factor_names else ("behavior",)
    covering_array = design.get("_covering_array")
    if not covering_array:
        covering_array = build_covering_array(covering_design, rng, axes=all_axes)

    # Shuffle so remainder budget is distributed randomly
    tuples = list(covering_array)
    rng.shuffle(tuples)

    base, remainder = divmod(sample_size, len(tuples))

    all_assignments: list[dict[str, str]] = []
    jobs: list[SeedJob] = []
    next_index = 0
    for i, row in enumerate(tuples):
        count = base + (1 if i < remainder else 0)
        if count == 0:
            continue

        behavior_name = row["behavior"]
        behavior_entry = behavior_level_lookup[behavior_name]
        spec: dict[str, dict[str, Any]] = {
            factor_name: level_lookup[factor_name][row[factor_name]]
            for factor_name in factor_names
        }
        spec["behavior"] = behavior_entry

        for _ in range(count):
            all_assignments.append(dict(row))

        jobs.append(
            SeedJob(
                order=len(jobs),
                behavior=behavior_entry,
                count=count,
                start_index=next_index,
                tuple_spec=spec,
            )
        )
        next_index += count

    return jobs, all_assignments or None


# ---------------------------------------------------------------------------
# Unified generation
# ---------------------------------------------------------------------------
async def _generate_records(
    *,
    kind: str,
    policy: dict[str, Any],
    model: str,
    sample_size: int,
    temperature: float | None,
    max_tokens: int | None,
    reasoning_effort: str | None = None,
    timeout_s: float | None,
    tool_source: str = TOOL_SOURCE_RUNTIME,
    fixed_system_prompt: str | None = None,
    context: str | None = None,
    design: dict[str, Any] | None = None,
    seed: int = 0,
    concurrency: int = 8,
) -> list[dict[str, Any]]:
    """Call the LLM once per covering-array tuple and return records."""
    if sample_size <= 0 or sample_size > 100_000:
        raise ValueError(f"{kind} sample_size must be between 1 and 100000")
    if concurrency <= 0:
        raise ValueError("seed generation concurrency must be > 0")

    design_data = design or {}
    schema = seeds_response_schema(tool_source)
    seed_id_prefix = SEED_ID_PREFIX[kind]
    concept_name = policy.get("concept", {}).get("name", "concept")

    jobs, _ = build_generation_jobs(
        policy=policy,
        design=design_data,
        sample_size=sample_size,
        rng=random.Random(seed),
    )
    factor_names = design_factors(design_data)

    async def _process(job: SeedJob) -> dict[str, Any]:
        prompt = build_generation_prompt(
            kind=kind,
            policy=policy,
            behavior=job.behavior,
            count=job.count,
            context=context,
            design=design_data,
            tuple_spec=job.tuple_spec,
            tool_source=tool_source,
        )
        slug = slugify(str(job.behavior.get("name") or ""))
        behavior_name = str(job.behavior.get("name") or "")

        response = await generate_structured(
            model,
            prompt,
            schema_name=f"{kind}_seeds",
            json_schema=schema,
            options=GenerateOptions(
                temperature=temperature, max_tokens=max_tokens,
                reasoning_effort=reasoning_effort, timeout_s=timeout_s,
            ),
        )
        payload = response.parsed
        if not isinstance(payload, dict) or not isinstance(payload.get("seeds"), list):
            raise ValueError(f"{kind} seed generation returned invalid seeds payload")

        returned_seeds = payload["seeds"]
        if len(returned_seeds) < job.count:
            raise ValueError(
                f"{kind} generation returned {len(returned_seeds)} seeds "
                f"for a batch of {job.count}"
            )
        returned_seeds = returned_seeds[:job.count]

        records = []
        for idx, raw_seed in enumerate(returned_seeds):
            if not isinstance(raw_seed, dict):
                raise ValueError(f"{kind} generation returned non-dict seed at index {idx}")
            factors: dict[str, str] = {"behavior": behavior_name}
            if job.tuple_spec:
                factors.update({
                    factor_name: job.tuple_spec[factor_name]["name"]
                    for factor_name in factor_names
                })
            records.append(
                seed_record(
                    kind=kind, seed_id=f"{seed_id_prefix}-{slug}-{job.start_index + idx:03d}",
                    concept=concept_name,
                    seed_payload=normalize_generated_seed(
                        raw_seed,
                        tool_source=tool_source,
                        fixed_system_prompt=fixed_system_prompt,
                    ),
                    factors=factors,
                )
            )
        return {"order": job.order, "records": records}

    results = await gather_limited(jobs, limit=concurrency, worker=_process)
    ordered = sorted(results, key=lambda r: r["order"])
    return [rec for r in ordered for rec in r["records"]]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def run_seeds(
    *,
    policy_path: str,
    save_path: str,
    context: str | None,
    prompt: dict[str, Any] | None,
    scenario: dict[str, Any] | None,
    target: TargetConfig | None,
    tool_source: str = TOOL_SOURCE_RUNTIME,
    design: dict[str, Any] | None = None,
    seed: int = 0,
    concurrency: int = 8,
) -> dict[str, Any]:
    """Generate prompt and/or scenario seeds."""
    if prompt is None and scenario is None:
        raise ValueError("seeds stage requires prompt and/or scenario configuration")
    _validate_tool_source(tool_source, target)

    policy = load_policy(policy_path)
    out_path = resolve_path(save_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_context = normalize_seed_context(context)
    fixed_system_prompt = (str(target.system_prompt or "").strip() or None) if target is not None else None

    design = normalize_design(design or {}, policy, inject_behavior=True)

    kinds_cfgs: list[tuple[str, dict[str, Any]]] = []
    if prompt is not None:
        kinds_cfgs.append(("prompt", prompt))
    if scenario is not None:
        kinds_cfgs.append(("scenario", scenario))

    results = await asyncio.gather(*[
        _generate_records(
            kind=kind, policy=policy, model=str(cfg["model"]),
            sample_size=int(cfg["sample_size"]),
            temperature=(
                float(cfg["temperature"])
                if cfg.get("temperature") is not None
                else None
            ),
            max_tokens=(
                int(cfg["max_tokens"])
                if cfg.get("max_tokens") is not None
                else None
            ),
            reasoning_effort=cfg.get("reasoning_effort"),
            timeout_s=cfg.get("timeout_s"),
            tool_source=tool_source, fixed_system_prompt=fixed_system_prompt,
            context=normalized_context,
            design=design,
            seed=seed,
            concurrency=concurrency,
        )
        for kind, cfg in kinds_cfgs
    ])

    all_records = [rec for records in results for rec in records]
    all_records = normalize_seed_rows(all_records)
    write_jsonl(out_path, all_records)
    prompt_count = sum(
        1 for record in all_records if record.get("kind") == "prompt"
    )
    scenario_count = sum(
        1 for record in all_records if record.get("kind") == "scenario"
    )
    return {
        "seeds_path": str(out_path),
        "saved_count": len(all_records),
        "prompt_count": prompt_count,
        "scenario_count": scenario_count,
    }


def _parse_kind_config(
    raw_cfg: dict[str, Any], kind: str, raw: dict[str, Any],
    *, sample_size: int, temperature: float, max_tokens: int,
) -> dict[str, Any]:
    """Validate and build a normalized config dict for a seed generation kind."""
    if not isinstance(raw, dict):
        raise ValueError(f"seeds.{kind} must be a mapping")
    if "budget" in raw:
        raise ValueError(f"seeds.{kind}.budget was renamed to seeds.{kind}.sample_size")
    reject_unknown_keys(
        raw,
        field_name=f"seeds.{kind}",
        allowed={"model", "sample_size", "timeout_s"},
    )
    model = raw.get("model") or raw_cfg.get("model")
    if not isinstance(model, dict):
        raise ValueError(f"seeds.{kind}.model must be a mapping")
    model_cfg = parse_model_config(
        model,
        field_name=f"seeds.{kind}.model",
        default_temperature=temperature,
        default_max_tokens=max_tokens,
    )
    return {
        "model": model_cfg.name,
        "sample_size": raw.get("sample_size", sample_size),
        "temperature": model_cfg.temperature,
        "max_tokens": model_cfg.max_tokens,
        "reasoning_effort": model_cfg.reasoning_effort,
        "timeout_s": raw.get("timeout_s") or raw_cfg.get("timeout_s"),
    }


async def run(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
    """Stage entry point — parse raw YAML config, validate, and delegate to run_seeds."""
    context = ctx.get("context")
    if context is not None and not isinstance(context, str):
        raise ValueError("context must be a string when provided")
    reject_unknown_keys(
        raw_cfg,
        field_name="seeds",
        allowed={
            "policy_path",
            "save_path",
            "design_path",
            "tool_source",
            "model",
            "timeout_s",
            "prompt",
            "scenario",
            "validators",
            "validator_model",
        },
    )
    if "validators" in raw_cfg or "validator_model" in raw_cfg:
        raise ValueError("seeds validators are no longer supported")
    tool_source = raw_cfg.get("tool_source", TOOL_SOURCE_RUNTIME)
    if not isinstance(tool_source, str):
        raise ValueError("seeds.tool_source must be a string")

    prompt_cfg = None
    scenario_cfg = None

    if raw_prompt := raw_cfg.get("prompt"):
        prompt_cfg = _parse_kind_config(
            raw_cfg, "prompt", raw_prompt,
            sample_size=100, temperature=DEFAULT_GENERATION_TEMPERATURE, max_tokens=DEFAULT_GENERATION_MAX_TOKENS,
        )

    if raw_scenario := raw_cfg.get("scenario"):
        if "modality" in raw_scenario:
            raise ValueError("seeds.scenario.modality is no longer supported; use seeds.tool_source")
        scenario_cfg = _parse_kind_config(
            raw_cfg, "scenario", raw_scenario,
            sample_size=100, temperature=DEFAULT_GENERATION_TEMPERATURE, max_tokens=DEFAULT_GENERATION_MAX_TOKENS,
        )

    if prompt_cfg is None and scenario_cfg is None:
        raise ValueError("seeds requires prompt and/or scenario configuration")

    path_cfg = {
        "policy_path": raw_cfg.get("policy_path") or str(Path(ctx["suite_root"]) / "policy.json"),
        "save_path": raw_cfg.get("save_path") or str(Path(ctx["suite_root"]) / SEEDS_FILE),
        "design_path": raw_cfg.get("design_path") or str(Path(ctx["suite_root"]) / "design.json"),
    }

    cfg = resolve_stage_paths(
        path_cfg,
        cfg_path=ctx["config_path"],
        artifacts_root=ctx["artifacts_root"],
    )
    policy_path = cfg["policy_path"]
    design_path = Path(cfg["design_path"])
    if design_path.exists():
        raw_design = json.loads(design_path.read_text(encoding="utf-8"))
    elif ctx.get("factors") in (None, []):
        raw_design = {}
    else:
        raise ValueError(
            f"seed generation requires a design at {design_path}. "
            f"Run the design stage first."
        )

    kinds = []
    if prompt_cfg is not None:
        kinds.append(f"prompt(n={prompt_cfg['sample_size']})")
    if scenario_cfg is not None:
        kinds.append(f"scenario(n={scenario_cfg['sample_size']})")
    log.debug(f"seeds: kinds=[{', '.join(kinds)}], tool_source={tool_source}")

    result = await run_seeds(
        policy_path=policy_path,
        save_path=cfg["save_path"],
        context=context,
        prompt=prompt_cfg,
        scenario=scenario_cfg,
        target=ctx.get("target"),
        tool_source=tool_source,
        design=raw_design,
    )
    return {
        "seeds_path": result["seeds_path"],
        "_summary": {
            "total": result.get("saved_count", 0),
            "prompts": result.get("prompt_count", 0),
            "scenarios": result.get("scenario_count", 0),
        },
    }
