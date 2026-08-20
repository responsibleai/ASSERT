---
# Copy this file to a temporary working path and replace every <placeholder>.
# Duplicate the pass block until each cycle contains exactly n passes.
schema_version: 1
harm_name: "<harm_name>"
n: 1
active_cycle: cycle-1
evaluation_intent:
  decision: null
  purposes: []
  population: null
references:
  "[1]":
    title: "<source title>"
    url: "<retrieved URL or repo-relative preset path>"
    accessed: "<YYYY-MM-DD>"
  "[2]":
    title: "<independent source title>"
    url: "<retrieved URL or repo-relative preset path>"
    accessed: "<YYYY-MM-DD>"
cycles:
  - id: cycle-1
    criteria_version: criteria-v1
    criteria:
      - none
    status: pending_review
    passes:
      - number: 1
        complete: true
        intent_fields_applied: []
        search_branches:
          - "<query family, source path, or citation-snowball branch>"
        breadth_audit_complete: true
        no_new_dimension_passes: 2
        candidates:
          behavior_categories:
            - id: p1-behavior-1
              name: "<behavior category>"
              disposition: keep
              citation_tags: ["[1]"]
          test_dimensions:
            - id: p1-test-1
              name: "<test-set dimension>"
              disposition: keep
              citation_tags: ["[1]", "[2]"]
          judge_dimensions:
            - id: p1-judge-1
              name: "<judge dimension>"
              disposition: keep
              citation_tags: ["[1]", "[2]"]
    deduplication:
      completed: true
      duplicate_audit_complete: true
      namespaces:
        behavior_categories:
          - id: behavior-1
            name: "<canonical behavior category>"
            purpose: "<what this category distinguishes>"
            levels_or_mode: "permissible or non-permissible category"
            observability: "<single response, multi-turn, trajectory, or action>"
            executable: true
            aliases: []
            source_items: [p1-behavior-1]
            source_passes: [1]
            citation_tags: ["[1]"]
            rationale: "Retained as a distinct category."
            intent_alignment: null
        test_dimensions:
          - id: test-1
            name: "<canonical test-set dimension>"
            purpose: "<what case variation this dimension introduces>"
            levels_or_mode: "<explicit levels or generated-mode description>"
            observability: "<single response, multi-turn, trajectory, or action>"
            executable: true
            aliases: []
            source_items: [p1-test-1]
            source_passes: [1]
            citation_tags: ["[1]", "[2]"]
            rationale: "Retained as a distinct test-set dimension."
            intent_alignment: null
        judge_dimensions:
          - id: judge-1
            name: "<canonical judge dimension>"
            purpose: "<independently scorable outcome>"
            levels_or_mode: "rubric-scored"
            observability: "<single response, multi-turn, trajectory, or action>"
            executable: true
            aliases: []
            source_items: [p1-judge-1]
            source_passes: [1]
            citation_tags: ["[1]", "[2]"]
            rationale: "Retained as a distinct judge dimension."
            intent_alignment: null
      rejections: []
approval:
  status: pending
  cycle_id: cycle-1
  criteria_version: criteria-v1
  relevance: pending
  edits: ""
  response: ""
  approved_by: ""
  approved_at: null
---
# Dimension Review: <harm_name>

Fill the YAML frontmatter, then run the validator's `render` command. It will
replace this body with review tables generated from the active cycle.

## Evaluation Intent

| Field | Answer |
|---|---|
| Decision supported | not provided; default workflow used |
| Purpose(s) | not provided; default workflow used |
| System users/affected groups | not provided; default workflow used |

## Behavior Categories

| Name | Purpose | Intent alignment | Levels or mode | Observability | Executable | Sources | Passes |
|---|---|---|---|---|---|---|---|
| <canonical name> | <purpose> | none | <levels or mode> | <timescale> | yes | [1] | 1 |

## Test-Set Dimensions

| Name | Purpose | Intent alignment | Levels or mode | Observability | Executable | Sources | Passes |
|---|---|---|---|---|---|---|---|
| <canonical name> | <purpose> | none | <levels or mode> | <timescale> | yes | [1], [2] | 1 |

## Judge Dimensions

| Name | Purpose | Intent alignment | Levels or mode | Observability | Executable | Sources | Passes |
|---|---|---|---|---|---|---|---|
| <canonical name> | <purpose> | none | rubric-scored | <timescale> | yes | [1], [2] | 1 |

## Approval

The rendered report asks the user to approve, revise, or regenerate the set and
to provide any additional edits or generation criteria.