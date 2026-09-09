# Prior-Generation Isolation

Run this preflight once the stable harm slug is known and before online
research, dimension generation, or reading any matching generated YAML. Its goal
is to prevent a new generation from inheriting assumptions, dimensions,
citations, model settings, or other content from an earlier run.

## G1. Discover by path only

Use the path-only planner. It traverses directory entries and counts `.yaml` and
`.yml` filenames; it never opens, parses, hashes, searches, or prints their
contents.

```bash
python .claude/skills/run-assert-eval/plan_generation_path.py \
  --eval-type harm \
  --name <stable_slug> \
  --root examples/<domain>
```

**Scope the root to the domain that owns the eval.** Two domains may legitimately
share a risk name — `budget_overrun` and `fabricated_travel_details` each appear
under both `phoenix_auto_trace` and `travel_planner_langgraph` — so a repo-wide
`--root examples` would report one domain's config as a prior generation of the
other's and force a spurious dated directory. A domain-scoped root compares like
with like. Matching generation directories are limited to `<risk>`,
`<risk>_YYYY-MM-DD`, and same-day ordinal variants such as `<risk>_YYYY-MM-DD_2`.

Treat the planner's JSON as path metadata only:

- `prior_generation_directories` lists matching directories and YAML filename
  counts without exposing file contents;
- `requires_confirmation` says whether a prior YAML generation or an unsafe
  path type was found; and
- `proposed_directory` is a path that did not exist when the planner ran.

Do not replace this helper with a content-search tool. For matching prior YAMLs,
never call `read_file`, a parser, `load_config`, `cat`, `sed`, `head`, content
grep/search, `git diff`, `git show`, `git blame`, hashing, or any command that
could inspect or reveal their contents. Do not infer content from file size,
timestamps, commit history, generated artifacts, or surrounding reports.

## G2. Ask before regenerating

When `requires_confirmation` is true, stop before research and tell the user:

- matching generation directories already exist;
- how many YAML filenames were found in each directory;
- their contents were not inspected; and
- the planner's proposed new directory.

Ask the user to choose exactly one action, preferably with a structured question:

1. **Regenerate in the proposed isolated directory**; or
2. **Exit without generating**.

Do not treat silence or an unrelated response as permission. If the user exits,
perform no research and write no generation artifacts. If a matching directory
exists but contains no YAML filename, do not call it a prior generation; reserve
the path and use the planner's non-colliding proposal.

## G3. Select the run directory

With no prior matching generation, use the unsuffixed directory
`examples/<domain>/<risk>/`. When the user approves regeneration, use the proposed dated
directory `examples/<domain>/<risk>_YYYY-MM-DD/`. If that date already exists, the planner
adds `_2`, `_3`, and so on while preserving the date.

Record the selected directory once and use it consistently for the config,
review ledger, approval stamp, final command, and report. Run the planner again
immediately before pre-write if enough time has passed for another process to
claim the path. The validator's pre-write gate must reject an existing config
path without reading it; never overwrite or replace a config from any run.

## G4. Preserve research independence

Never use a prior matching generated YAML to seed, constrain, compare, validate,
or deduplicate the new run. In particular, do not reuse its behavior description,
categories, dimensions, levels, rubrics, references, target settings, sample
sizes, model values, or generation knobs.

Curated repository sources remain allowed because they are inputs rather than
prior generated eval configs:

- `assert_ai/library/behaviors/`;
- `assert_ai/library/judges/`;
- `examples/behavior_specs/`;
- the bundled skill assets and schema documentation; and
- system/context information supplied directly by the user.

After the new file is created in its isolated directory, inspect and validate
that new file normally. The prohibition continues to apply to every earlier
matching generation.