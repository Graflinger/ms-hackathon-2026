# Dataset and Feedback Design

This document defines the canonical dataset model and review workflow. Publication requires approved case revisions.

## One canonical representation

Spreadsheets are an input convenience; versioned structured cases are the internal and exported source of truth.

| Entity | Main fields / purpose |
|---|---|
| Case revision | Stable case ID, revision, title, tags, source, scenario context, ordered turns, expectations, check references, fixtures |
| Observation | Case revision or chat session, agent revision, actual messages, observable tool spans, usage/latency, trace completeness |
| Feedback | Reviewer, time, observed answer/call reference, issue type, comment, unreviewed correction, review status |
| Dataset release | Immutable selection of approved case revisions, schema version, content hash, publisher/time |
| Evaluation run | Dataset release, agent/evaluator/judge versions, observations, check results, gate result |

Keep reference answers distinct from generated answers. A reference answer may illustrate acceptable content rather than require identical wording. Cases without enough expectations can remain candidates but must not silently become passing golden tests.

## Excel/CSV import

Simple template: `case_id` (optional), `question`, `reference_answer`, `scenario`, `tags`. At least an input is required to save a candidate; publication requires actionable reviewed expectations.

For multi-turn input add `scenario_id` and `turn_index`; each row supplies a scripted user turn and optional expected answer. Validate grouping and ordering. Add tool expectations in the UI; spreadsheet import of nested tool expectations is outside the MVP.

Provide column mapping, preview, row errors, blank/duplicate handling, encoding checks, source file/sheet/row metadata, and deliberate merge-versus-new-case selection. Do not execute formulas or macros; flag formula cells that cannot be reliably read. Escape spreadsheet formula injection when exporting human-readable CSV.

## Feedback to test conversion

```text
Import or captured interaction
    -> candidate case + linked observation/feedback
    -> reviewer edits expected behavior/checks
    -> approved case revision
    -> published golden release
```

Feedback can target an answer, a specific observed call, or a missing call at a turn. Support wrong tool, wrong/missing parameter, forbidden call, incorrect result handling, and unexpected extra calls. A missing expected call has no observed span ID, so it needs a turn-level target.

Example: observed `lookup_customer({"customer_id":"C-999"})`; reviewer verifies the scenario requires `C-123`. Preserve the observed call and add a separate expected assertion on `customer_id`. Do not replace trace history or use the bad answer as the reference answer.

A reviewer can accept a correction, reject feedback, or leave it unresolved. Detect repeated cases for review rather than silently duplicating them. Keep reviewer attribution and edit reasons.

## Expressing tool expectations

Match tool expectations using semantic constraints, not exact trace snapshots:

- Required/forbidden tool name, turn scope, occurrence bounds
- Argument-path checks: exact, subset, type/schema, range, or explicit predicate from the supported declarative set
- Allowed alternatives and order constraints only where order matters
- Expected treatment of tool failures and returned content

Execution span IDs anchor feedback, but future runs match expectations by turn/tool constraints, not old span IDs. Live timestamps, generated IDs, and nonessential argument fields should not make tests brittle. Define matching for repeated/parallel calls explicitly.

## Multi-turn replay

Preserve necessary context and tool fixtures. Full-conversation regression should feed scripted user turns and newly generated assistant responses forward. Do not insert the golden assistant answer as the preceding response; that hides propagation failures. If testing a single turn against fixed historical context, label it as a distinct evaluation mode.

Start with scripted user turns. Autonomous user simulation is a separate later feature because it adds another source of nondeterminism.

## Changes and publication

Editing an approved case creates a new revision; editing a dataset creates a new release. Past runs and exported bundles retain their pinned references. CI upgrades are explicit, reviewed changes, not silent synchronization to the latest dataset.

Separate development cases from a held-out set for credible improvement claims. Export only authorized sanitized material; keep provenance and access rules. Required privacy deletion may override retention, leaving non-sensitive audit metadata where permitted.
