"""Generate prompt and scenario test_set for the unified pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import warnings
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
    stratification_dimensions,
    fill_template,
    load_policy,
    load_prompt_text,
    normalize_test_case_context,
    normalize_test_case_rows,
    resolve_path,
    slugify,
    write_jsonl,
)
from p2m.core.model_client import (
    GenerateOptions,
    LLMAuthError,
    LLMInputError,
    LLMProviderError,
    LLMRateLimitError,
    generate_structured,
)
from p2m.core.tools import normalize_tool_defs
from p2m.stages.stratification import DEFAULT_LEVEL_COUNT, normalize_stratification, run_stratification

BASE_DIR = Path(__file__).resolve().parents[2]
PROMPT_TEST_CASE_TEMPLATE = (BASE_DIR / "prompts" / "test_set_direct_single.md").read_text(encoding="utf-8")
SCENARIO_TEST_CASE_TEMPLATE = (BASE_DIR / "prompts" / "test_set_scenario_single.md").read_text(encoding="utf-8")
TEST_SET_FILE = "test_set.jsonl"
SCOPE = "suite"
SUITE_OUTPUT = TEST_SET_FILE

# Cap on the number of test_set requested per LLM call. Each scenario test case
# carries a system prompt, opening message, and dimension metadata
# (≈300-500 output tokens), so a batch of 15 routinely overflows the
# default 3000-token max_tokens budget and the response truncates mid-JSON.
# Splitting the per-tuple budget into chunks of MAX_TEST_CASES_PER_BATCH keeps
# every batch comfortably inside the budget, lifts effective concurrency
# (more, smaller jobs run in parallel), and lets per-batch failures lose
# fewer test_set at a time. Empirically observed at sample_size=1000 with
# 67 covering-array tuples (≈15 test_set per tuple): 65/67 batches truncated
# without this cap, only 30 valid test_set survived.
MAX_TEST_CASES_PER_BATCH = 5

TOOL_SOURCE_RUNTIME = "runtime"
TOOL_SOURCE_PER_TEST_CASE = "per_test_case"
TOOL_SOURCE_PER_TEST_CASE_LEGACY = "per_seed"  # accepted as deprecated alias

_VALID_TOOL_SOURCES = {TOOL_SOURCE_RUNTIME, TOOL_SOURCE_PER_TEST_CASE, TOOL_SOURCE_PER_TEST_CASE_LEGACY}


def _normalize_tool_source(value: str) -> str:
    """Map the deprecated `per_seed` alias to the canonical `per_test_case`."""
    if value == TOOL_SOURCE_PER_TEST_CASE_LEGACY:
        warnings.warn(
            "test_set.tool_source='per_seed' is deprecated; use 'per_test_case'.",
            DeprecationWarning,
            stacklevel=3,
        )
        return TOOL_SOURCE_PER_TEST_CASE
    return value

TEST_CASE_ID_PREFIX = {"prompt": "ps", "scenario": "as"}
GENERATION_GUIDANCE_TEMPLATE = load_prompt_text("test_set_generation_guidance.md")
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
    """Build the JSON output-schema example for test-case generation prompts."""
    schema = TEST_CASE_SCHEMA_WITH_TOOLS if tool_source == TOOL_SOURCE_PER_TEST_CASE else TEST_CASE_SCHEMA
    field_examples = FIELD_EXAMPLES[kind]
    test_case: dict[str, Any] = {}
    for prop_name in schema.get("properties", {}):
        if prop_name in field_examples:
            test_case[prop_name] = field_examples[prop_name]
        elif prop_name == "tools":
            test_case["tools"] = [TOOL_FIELD_EXAMPLES]
    return json.dumps({"test_set": [test_case]}, indent=2)


def _validate_tool_source(tool_source: str, target: TargetConfig | None) -> None:
    if tool_source not in {TOOL_SOURCE_RUNTIME, TOOL_SOURCE_PER_TEST_CASE}:
        raise ValueError("test_set.tool_source must be 'runtime' or 'per_test_case'")
    if tool_source == TOOL_SOURCE_RUNTIME:
        return
    if target is None or not target.model:
        raise ValueError("test_set.tool_source=per_test_case requires target.model")
    if target.connector:
        raise ValueError("test_set.tool_source=per_test_case does not support target.connector")
    if target.tools is None or not target.tools.simulator:
        raise ValueError("test_set.tool_source=per_test_case requires target.tools.simulator")
    if target.tools.module:
        raise ValueError("test_set.tool_source=per_test_case does not support target.tools.module")
    if target.tools.toolset:
        raise ValueError("test_set.tool_source=per_test_case does not support target.tools.toolset")


# ---------------------------------------------------------------------------
# Schema & record builder
# ---------------------------------------------------------------------------

TEST_CASE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "system_prompt": {"type": "string"},
    },
    "required": ["title", "description", "system_prompt"],
}

TEST_CASE_SCHEMA_WITH_TOOLS: dict[str, Any] = {
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


def test_set_response_schema(
    tool_source: str = TOOL_SOURCE_RUNTIME,
    min_items: int | None = None,
    max_items: int | None = None,
) -> dict[str, Any]:
    """Full response schema wrapping test_set in a ``{"test_set": [...]}`` envelope.

    When ``min_items`` is provided, the schema requires the model to return at
    least that many test_set. Without this lower bound, gpt-5.4-mini frequently
    returns N-1 items for batches larger than ~10, since the schema previously
    allowed 0-2000 items regardless of the prompt's stated count.

    When ``max_items`` is provided, it overrides the default 2000 ceiling so
    callers can pin both bounds to the exact target count. Pinning both bounds
    keeps the schema symmetric with the prompt's stated count and prevents the
    model from over-generating in the (unlikely) inverse failure mode.
    """
    item_schema = TEST_CASE_SCHEMA_WITH_TOOLS if tool_source == TOOL_SOURCE_PER_TEST_CASE else TEST_CASE_SCHEMA
    resolved_max = max_items if (max_items is not None and max_items > 0) else 2000
    test_set_schema: dict[str, Any] = {
        "type": "array",
        "maxItems": resolved_max,
        "items": item_schema,
    }
    if min_items is not None and min_items > 0:
        test_set_schema["minItems"] = min_items
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "test_set": test_set_schema,
        },
        "required": ["test_set"],
    }


def normalize_generated_test_case(
    raw_test_case: dict[str, Any],
    *,
    tool_source: str,
    fixed_system_prompt: str | None,
) -> dict[str, Any]:
    payload = {
        "title": str(raw_test_case.get("title") or ""),
        "description": str(raw_test_case.get("description") or ""),
    }
    if not payload["description"].strip():
        raise ValueError("generated test case is missing description")

    system_prompt = str(raw_test_case.get("system_prompt") or "").strip()
    if system_prompt and fixed_system_prompt is None:
        payload["system_prompt"] = system_prompt

    raw_tools = raw_test_case.get("tools")
    if tool_source == TOOL_SOURCE_PER_TEST_CASE:
        if not isinstance(raw_tools, list) or not raw_tools:
            raise ValueError("generated test case requires non-empty tools when test_set.tool_source=per_test_case")
        try:
            normalize_tool_defs(raw_tools)
        except (KeyError, TypeError) as exc:
            raise ValueError("generated test case contains invalid tool definitions") from exc
        payload["tools"] = raw_tools
    elif raw_tools:
        raise ValueError("generated test case.tools is only allowed when test_set.tool_source=per_test_case")

    return payload


def test_case_record(
    *,
    kind: str,
    test_case_id: str,
    behavior: str,
    test_case_payload: dict[str, Any],
    dimensions: dict[str, str] | None = None,
) -> dict[str, Any]:
    record = {
        "type": kind,
        "test_case_id": test_case_id,
        "behavior": behavior,
        "seed": test_case_payload,
    }
    if dimensions:
        record["dimensions"] = dimensions
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
    TOOL_SOURCE_PER_TEST_CASE: """
## Tools (agentic modality)

This is an **agentic** audit. The target model will have access to tools during the conversation.
For each test case, define 1-5 realistic tools that the target model would have in this scenario.
""",
}


def _render_taxonomy_body(taxonomy: dict[str, Any]) -> str:
    return json.dumps(taxonomy, indent=2, ensure_ascii=False)


def _template_replacements(
    kind: str,
    behavior_spec_name: str,
    behavior_name: str,
    behavior_description: str,
    examples: list[str],
    count: int,
    *,
    context: str | None,
    batch_guidance: str,
    taxonomy_body: str,
    tool_source: str = TOOL_SOURCE_RUNTIME,
) -> dict[str, str]:
    return {
        "behavior": behavior_name,
        "definition": behavior_description,
        "count": str(count),
        "context": context or "",
        "examples": (
            "\n".join(f"- {e}" for e in examples)
            if examples
            else "- (no examples provided)"
        ),
        "taxonomy_body": taxonomy_body,
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
            f"- Taxonomy behavior: {tuple_spec['behavior']['name']} — {tuple_spec['behavior']['description']}"
        )
    return "\n".join(lines)


def build_generation_prompt(
    *,
    kind: str,
    taxonomy: dict[str, Any],
    behavior: dict[str, Any],
    count: int,
    context: str | None,
    stratification: dict[str, list[dict[str, Any]]],
    tuple_spec: dict[str, dict[str, Any]] | None = None,
    tool_source: str = TOOL_SOURCE_RUNTIME,
) -> str:
    """Build the full generation prompt for one tuple's batch.

    *kind*: ``"prompt"`` or ``"scenario"``.
    """
    behavior_spec_name = str(taxonomy.get("behavior", {}).get("name") or "behavior")
    behavior_name = str(behavior.get("name") or "").strip()
    behavior_description = str(behavior.get("description") or "").strip()
    policy_behavior = next(
        (
            entry
            for entry in taxonomy.get("behavior_categories", [])
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
        and (kind == "scenario" or not stratification_dimensions(stratification))
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
        behavior_spec_name,
        behavior_name,
        behavior_description,
        behavior_examples,
        count,
        context=context,
        batch_guidance=batch_guidance,
        taxonomy_body=_render_taxonomy_body(taxonomy),
        tool_source=tool_source,
    )
    return fill_template(
        PROMPT_TEST_CASE_TEMPLATE if kind == "prompt" else SCENARIO_TEST_CASE_TEMPLATE,
        replacements,
    )


# ---------------------------------------------------------------------------
# Covering array construction
# ---------------------------------------------------------------------------

def build_covering_array(
    stratification: dict[str, list[dict[str, str]]],
    rng: random.Random,
    *,
    axes: tuple[str, ...] | None = None,
) -> list[dict[str, str]]:
    """Build a strength-2 covering array for the stratification dimensions.

    Returns a set of tuples such that every pair of dimensions has all
    level-combinations represented at least once.  Each tuple is a
    flat dict mapping dimension name to level name.

    Uses IPOG (In-Parameter-Order-General) for construction.
    """
    active_axes = stratification_dimensions(stratification) if axes is None else tuple(axes)
    if not active_axes:
        raise ValueError("at least one dimension is required")

    cardinalities = [len(stratification[ax]) for ax in active_axes]
    if any(c == 0 for c in cardinalities):
        raise ValueError("all dimensions must have at least one level")

    if len(active_axes) < 2:
        return [{active_axes[0]: entry["name"]} for entry in stratification[active_axes[0]]]

    return _ipog(stratification, active_axes, rng)


def _ipog(
    stratification: dict[str, list[dict[str, str]]],
    active_axes: tuple[str, ...],
    rng: random.Random,
) -> list[dict[str, str]]:
    """IPOG (In-Parameter-Order-General) strength-2 covering array.

    Builds the array column-by-column: starts with a full cross of the
    first two dimensions, then extends one dimension at a time. For each new
    dimension, horizontal extension greedily assigns levels to existing
    rows to maximize pair coverage; vertical extension adds rows for any
    remaining uncovered pairs.
    """
    k = len(active_axes)
    levels = [[entry["name"] for entry in stratification[ax]] for ax in active_axes]

    # Full cross of first two dimensions
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
class TestCaseJob:
    """One covering-array tuple's test-case generation work unit."""
    order: int
    behavior: dict[str, Any]
    count: int
    start_index: int
    tuple_spec: dict[str, dict[str, Any]] | None = None


def build_generation_jobs(
    *,
    taxonomy: dict[str, Any],
    stratification: dict[str, list[dict[str, Any]]],
    sample_size: int,
    rng: random.Random,
) -> tuple[list[TestCaseJob], list[dict[str, str]] | None]:
    """Build generation jobs — one per covering-array tuple.

    Budget is spread evenly across tuples via divmod. Each job produces
    its allocated number of test_set for a single (behavior, dimension…) tuple.
    """
    behavior_entries = stratification["behavior"]
    factor_names = stratification_dimensions(stratification)
    level_lookup: dict[str, dict[str, dict[str, str]]] = {
        factor_name: {
            entry["name"]: entry for entry in stratification[factor_name]
        }
        for factor_name in factor_names
    }
    behavior_level_lookup = {
        str(entry["name"]): entry for entry in behavior_entries
    }
    covering_stratification = dict(stratification)
    covering_stratification["behavior"] = behavior_entries
    all_axes = ("behavior",) + factor_names if factor_names else ("behavior",)
    covering_array = stratification.get("_covering_array")
    if not covering_array:
        covering_array = build_covering_array(covering_stratification, rng, axes=all_axes)

    # Shuffle so remainder budget is distributed randomly
    tuples = list(covering_array)
    rng.shuffle(tuples)

    base, remainder = divmod(sample_size, len(tuples))

    all_assignments: list[dict[str, str]] = []
    jobs: list[TestCaseJob] = []
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

        # Split the per-tuple budget into chunks ≤ MAX_TEST_CASES_PER_BATCH so
        # each LLM call stays inside the default max_tokens budget. Each
        # chunk becomes its own TestCaseJob with a contiguous start_index slot
        # so generated test case IDs remain stable and unique within the tuple.
        chunk_offset = 0
        remaining = count
        while remaining > 0:
            chunk = min(MAX_TEST_CASES_PER_BATCH, remaining)
            jobs.append(
                TestCaseJob(
                    order=len(jobs),
                    behavior=behavior_entry,
                    count=chunk,
                    start_index=next_index + chunk_offset,
                    tuple_spec=spec,
                )
            )
            chunk_offset += chunk
            remaining -= chunk
        next_index += count

    return jobs, all_assignments or None


# ---------------------------------------------------------------------------
# Unified generation
# ---------------------------------------------------------------------------
async def _generate_records(
    *,
    kind: str,
    taxonomy: dict[str, Any],
    model: str,
    sample_size: int,
    temperature: float | None,
    max_tokens: int | None,
    reasoning_effort: str | None = None,
    timeout_s: float | None,
    tool_source: str = TOOL_SOURCE_RUNTIME,
    fixed_system_prompt: str | None = None,
    context: str | None = None,
    stratification: dict[str, Any] | None = None,
    seed: int = 0,
    concurrency: int = 8,
) -> dict[str, Any]:
    """Call the LLM once per covering-array tuple and return records.

    Returns a dict with two keys:

    * ``records``: the flattened, in-order list of successfully generated
      test case records. May be a partial list if some batches failed.
    * ``errored_count``: number of batches that failed and produced no
      records. Surfaced upward so the runner / metrics can report
      partial-success runs.

    Raises only when *every* batch failed — that signals a systemic
    problem (auth, schema, config) rather than transient provider noise.
    """
    if sample_size <= 0 or sample_size > 100_000:
        raise ValueError(f"{kind} sample_size must be between 1 and 100000")
    if concurrency <= 0:
        raise ValueError("test-case generation concurrency must be > 0")

    stratification_data = stratification or {}
    test_case_id_prefix = TEST_CASE_ID_PREFIX[kind]
    behavior_spec_name = taxonomy.get("behavior", {}).get("name", "behavior")

    jobs, _ = build_generation_jobs(
        taxonomy=taxonomy,
        stratification=stratification_data,
        sample_size=sample_size,
        rng=random.Random(seed),
    )
    factor_names = stratification_dimensions(stratification_data)

    async def _process(job: TestCaseJob) -> dict[str, Any]:
        """Generate one batch of test_set for *job*.

        Per-batch failures (rate limit / provider 5xx / malformed JSON
        payloads / fewer-than-requested test_set) are caught and returned
        as a structured ``error`` sentinel rather than re-raised, so a
        single bad batch cannot kill the entire test_set stage and discard
        the work the other concurrent batches have already finished.
        Auth errors still propagate immediately — they are never
        transient and continuing only burns tokens.
        """
        slug = slugify(str(job.behavior.get("name") or ""))
        behavior_name = str(job.behavior.get("name") or "")
        try:
            prompt = build_generation_prompt(
                kind=kind,
                taxonomy=taxonomy,
                behavior=job.behavior,
                count=job.count,
                context=context,
                stratification=stratification_data,
                tuple_spec=job.tuple_spec,
                tool_source=tool_source,
            )
            job_schema = test_set_response_schema(
                tool_source, min_items=job.count, max_items=job.count
            )

            response = await generate_structured(
                model,
                prompt,
                schema_name=f"{kind}_test_cases",
                json_schema=job_schema,
                options=GenerateOptions(
                    temperature=temperature, max_tokens=max_tokens,
                    reasoning_effort=reasoning_effort, timeout_s=timeout_s,
                    call_label=f"test_set:{kind}:{slug}",
                ),
            )
            payload = response.parsed
            if not isinstance(payload, dict) or not isinstance(payload.get("test_set"), list):
                raise ValueError(f"{kind} test-case generation returned invalid test_set payload")

            returned_test_cases = payload["test_set"]
            if len(returned_test_cases) < job.count:
                raise ValueError(
                    f"{kind} generation returned {len(returned_test_cases)} test_set "
                    f"for a batch of {job.count}"
                )
            returned_test_cases = returned_test_cases[:job.count]

            records = []
            for idx, raw_test_case in enumerate(returned_test_cases):
                if not isinstance(raw_test_case, dict):
                    raise ValueError(f"{kind} generation returned non-dict test case at index {idx}")
                dimensions: dict[str, str] = {"behavior": behavior_name}
                if job.tuple_spec:
                    dimensions.update({
                        factor_name: job.tuple_spec[factor_name]["name"]
                        for factor_name in factor_names
                    })
                records.append(
                    test_case_record(
                        kind=kind, test_case_id=f"{test_case_id_prefix}-{slug}-{job.start_index + idx:03d}",
                        behavior=behavior_spec_name,
                        test_case_payload=normalize_generated_test_case(
                            raw_test_case,
                            tool_source=tool_source,
                            fixed_system_prompt=fixed_system_prompt,
                        ),
                        dimensions=dimensions,
                    )
                )
            return {"order": job.order, "records": records}
        except LLMAuthError:
            raise
        except (LLMInputError, LLMRateLimitError, LLMProviderError) as exc:
            log.warning(
                "Test Set %s job for behavior %r exhausted retries (%s): %s",
                kind, behavior_name, type(exc).__name__, exc,
            )
            return {"order": job.order, "records": [], "error": exc}
        except (json.JSONDecodeError, ValueError) as exc:
            log.warning(
                "Test Set %s job for behavior %r returned invalid payload (%s): %s",
                kind, behavior_name, type(exc).__name__, exc,
            )
            return {"order": job.order, "records": [], "error": exc}
        except Exception as exc:
            log.exception(
                "Test Set %s job for behavior %r failed unexpectedly",
                kind, behavior_name,
            )
            return {"order": job.order, "records": [], "error": exc}

    results = await gather_limited(jobs, limit=concurrency, worker=_process)
    ordered = sorted(results, key=lambda r: r["order"])
    records = [rec for r in ordered for rec in r["records"]]
    errors = [r["error"] for r in ordered if "error" in r]

    # Per-batch failures should not kill the stage as long as *some*
    # records were produced. The errored count is surfaced in the return
    # so the runner / benchmark CSV / metrics can show how many batches
    # were lost. The stage only fails outright when every batch failed —
    # that means the failure is systemic (auth, config, schema) rather
    # than transient.
    if errors and not records:
        log.error(
            "Test Set stage failed for kind=%s: all %d batch(es) errored",
            kind, len(errors),
        )
        raise errors[0]
    if errors:
        log.warning(
            "Test Set %s completed with %d batch failure(s) out of %d; produced %d records",
            kind, len(errors), len(jobs), len(records),
        )
    return {"records": records, "errored_count": len(errors)}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def run_test_set(
    *,
    taxonomy_path: str,
    save_path: str,
    context: str | None,
    prompt: dict[str, Any] | None,
    scenario: dict[str, Any] | None,
    target: TargetConfig | None,
    tool_source: str = TOOL_SOURCE_RUNTIME,
    stratification: dict[str, Any] | None = None,
    seed: int = 0,
    concurrency: int = 8,
) -> dict[str, Any]:
    """Generate prompt and/or scenario test_set."""
    if prompt is None and scenario is None:
        raise ValueError("test_set stage requires prompt and/or scenario configuration")
    _validate_tool_source(tool_source, target)

    taxonomy = load_policy(taxonomy_path)
    out_path = resolve_path(save_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_context = normalize_test_case_context(context)
    fixed_system_prompt = (str(target.system_prompt or "").strip() or None) if target is not None else None

    stratification = normalize_stratification(stratification or {}, taxonomy, inject_behavior=True)

    kinds_cfgs: list[tuple[str, dict[str, Any]]] = []
    if prompt is not None:
        kinds_cfgs.append(("prompt", prompt))
    if scenario is not None:
        kinds_cfgs.append(("scenario", scenario))

    results = await asyncio.gather(*[
        _generate_records(
            kind=kind, taxonomy=taxonomy, model=str(cfg["model"]),
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
            stratification=stratification,
            seed=seed,
            concurrency=concurrency,
        )
        for kind, cfg in kinds_cfgs
    ])

    all_records = [rec for r in results for rec in r["records"]]
    errored_count = sum(int(r.get("errored_count", 0)) for r in results)
    all_records = normalize_test_case_rows(all_records)
    write_jsonl(out_path, all_records)
    prompt_count = sum(
        1 for record in all_records if record.get("type") == "prompt"
    )
    scenario_count = sum(
        1 for record in all_records if record.get("type") == "scenario"
    )
    return {
        "test_set_path": str(out_path),
        "saved_count": len(all_records),
        "prompt_count": prompt_count,
        "scenario_count": scenario_count,
        # Number of test-case batches that failed across all kinds. Failed
        # batches produce no records, so a non-zero value here means the
        # caller may want to re-run to fill the gap.
        "errored_count": errored_count,
    }


def _parse_kind_config(
    raw_cfg: dict[str, Any], kind: str, raw: dict[str, Any],
    *, sample_size: int, temperature: float, max_tokens: int,
) -> dict[str, Any]:
    """Validate and build a normalized config dict for a test-case generation kind."""
    if not isinstance(raw, dict):
        raise ValueError(f"test_set.{kind} must be a mapping")
    if "budget" in raw:
        raise ValueError(f"test_set.{kind}.budget was renamed to test_set.{kind}.sample_size")
    reject_unknown_keys(
        raw,
        field_name=f"test_set.{kind}",
        allowed={"model", "sample_size", "timeout_s"},
    )
    model = raw.get("model") or raw_cfg.get("model")
    if not isinstance(model, dict):
        raise ValueError(f"test_set.{kind}.model must be a mapping")
    model_cfg = parse_model_config(
        model,
        field_name=f"test_set.{kind}.model",
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
    """Stage entry point — parse raw YAML config, validate, and delegate to run_test_set."""
    context = ctx.get("context")
    if context is not None and not isinstance(context, str):
        raise ValueError("context must be a string when provided")
    reject_unknown_keys(
        raw_cfg,
        field_name="test_set",
        allowed={
            "taxonomy_path",
            "save_path",
            "stratify",
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
        raise ValueError("test_set validators are no longer supported")
    tool_source = raw_cfg.get("tool_source", TOOL_SOURCE_RUNTIME)
    if not isinstance(tool_source, str):
        raise ValueError("test_set.tool_source must be a string")
    if tool_source not in _VALID_TOOL_SOURCES:
        raise ValueError("test_set.tool_source must be 'runtime' or 'per_test_case'")
    tool_source = _normalize_tool_source(tool_source)

    prompt_cfg = None
    scenario_cfg = None

    if raw_prompt := raw_cfg.get("prompt"):
        prompt_cfg = _parse_kind_config(
            raw_cfg, "prompt", raw_prompt,
            sample_size=100, temperature=DEFAULT_GENERATION_TEMPERATURE, max_tokens=DEFAULT_GENERATION_MAX_TOKENS,
        )

    if raw_scenario := raw_cfg.get("scenario"):
        if "modality" in raw_scenario:
            raise ValueError("test_set.scenario.modality is no longer supported; use test_set.tool_source")
        scenario_cfg = _parse_kind_config(
            raw_cfg, "scenario", raw_scenario,
            sample_size=100, temperature=DEFAULT_GENERATION_TEMPERATURE, max_tokens=DEFAULT_GENERATION_MAX_TOKENS,
        )

    if prompt_cfg is None and scenario_cfg is None:
        raise ValueError("test_set requires prompt and/or scenario configuration")

    stratify_raw = raw_cfg.get("stratify") or {}
    if not isinstance(stratify_raw, dict):
        raise ValueError("test_set.stratify must be a mapping")
    reject_unknown_keys(
        stratify_raw,
        field_name="test_set.stratify",
        allowed={"dimensions", "level_count", "model"},
    )
    dimensions = stratify_raw.get("dimensions", ctx.get("dimensions"))
    level_count = stratify_raw.get("level_count", DEFAULT_LEVEL_COUNT)
    if not isinstance(level_count, int) or level_count <= 0:
        raise ValueError("test_set.stratify.level_count must be a positive integer")
    stratify_model_raw = stratify_raw.get("model") or raw_cfg.get("model")
    stratify_model_cfg = None
    if stratify_model_raw is not None:
        stratify_model_cfg = parse_model_config(
            stratify_model_raw,
            field_name="test_set.stratify.model",
        )

    path_cfg = {
        "taxonomy_path": raw_cfg.get("taxonomy_path") or ctx.get("taxonomy_path") or str(Path(ctx["suite_root"]) / "taxonomy.json"),
        "save_path": raw_cfg.get("save_path") or ctx.get("test_set_path") or str(Path(ctx["suite_root"]) / TEST_SET_FILE),
    }

    cfg = resolve_stage_paths(
        path_cfg,
        cfg_path=ctx["config_path"],
        artifacts_root=ctx["artifacts_root"],
    )
    taxonomy_path = cfg["taxonomy_path"]
    stratification_dir = Path(cfg["save_path"]).parent
    stratification_result = await run_stratification(
        taxonomy_path=taxonomy_path,
        out_dir=str(stratification_dir),
        dimensions=dimensions,
        context=context,
        model=stratify_model_cfg.name if stratify_model_cfg is not None else None,
        level_count=level_count,
        reasoning_effort=stratify_model_cfg.reasoning_effort if stratify_model_cfg is not None else None,
        temperature=stratify_model_cfg.temperature if stratify_model_cfg is not None else None,
    )
    stratification_path = Path(stratification_result["stratification_path"])
    raw_stratification = json.loads(stratification_path.read_text(encoding="utf-8"))

    kinds = []
    if prompt_cfg is not None:
        kinds.append(f"prompt(n={prompt_cfg['sample_size']})")
    if scenario_cfg is not None:
        kinds.append(f"scenario(n={scenario_cfg['sample_size']})")
    log.debug(f"test_set: kinds=[{', '.join(kinds)}], tool_source={tool_source}")

    result = await run_test_set(
        taxonomy_path=taxonomy_path,
        save_path=cfg["save_path"],
        context=context,
        prompt=prompt_cfg,
        scenario=scenario_cfg,
        target=ctx.get("target"),
        tool_source=tool_source,
        stratification=raw_stratification,
    )
    return {
        "test_set_path": result["test_set_path"],
        "stratification_path": str(stratification_path),
        "_summary": {
            "factor_sizes": stratification_result.get("factor_sizes", {}),
            "total": result.get("saved_count", 0),
            "prompts": result.get("prompt_count", 0),
            "scenarios": result.get("scenario_count", 0),
            # Surfaced so the runner can skip finalize_artifact_plan when
            # any batch failed: a partial test_set.jsonl must not be cached
            # as a complete artifact (a future cache hit would silently
            # reuse the smaller-than-requested file). The runner gates
            # cacheable finalization on this value.
            "errored_count": int(result.get("errored_count", 0) or 0),
        },
    }
