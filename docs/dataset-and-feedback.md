# Dataset and Feedback Design

This document defines the canonical dataset model and review workflow. Publication requires approved case revisions.

## Project ownership

The delivered [project/agent model](projects-and-agents.md) scopes imports, cases, and releases to projects, not individual agents. Feedback inherits its observed session/revision and creates a candidate in that project. A common release can evaluate multiple compatible agents; incompatible selections are rejected, not silently reduced. Scope covers indirect feedback, release, result, and event lookups as well as lists.

Ownership is relational/API metadata, not a canonical `Case` field. Migration `0002` leaves canonical payloads, release hashes, and historical observations unchanged. Case IDs deliberately remain globally unique; exact context/turn duplicate detection is project-local. Use import policy `new` for repeated spreadsheet IDs across projects, preserving supplied IDs in provenance. There is no project-local external-ID namespace, cross-project move, or shared release.

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

The importer provides mapping, preview, row errors, blank/duplicate handling, encoding checks, and source file/sheet/row metadata. Policies are `reject` or `new`, not merge/update-existing. Imports are atomic; correct errors in the source and preview again. Formula-like CSV values and XLSX formulas/macros/external links are rejected, not executed. Sanitized parsed previews are persisted; raw uploads are not retained. Current exports are JSON-based ZIP bundles, not human-readable CSV.

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

A reviewer can accept or reject feedback or leave it unresolved. Accepting a signal and converting it to a candidate does not approve its expectations. The candidate retains user-turn context and provenance; a reviewer authors actionable checks before approval. Semantic duplicate detection is not implemented. Attribution currently uses the fixed local identity, not separate authenticated reviewers.

## Expressing tool expectations

Match tool expectations using semantic constraints, not exact trace snapshots:

- Required/forbidden tool name, turn scope, occurrence bounds
- Argument-path checks: exact, subset, type/schema, range, or explicit predicate from the supported declarative set
- Allowed alternatives and order constraints only where order matters
- Assert expected answer treatment of returned content using content/judge checks; observed tool errors remain execution evidence, not a separate general-purpose result-check language

Execution span IDs anchor feedback, but future runs match expectations by turn/tool constraints, not old span IDs. Live timestamps, generated IDs, and nonessential argument fields should not make tests brittle. Define matching for repeated/parallel calls explicitly.

## Multi-turn replay

Preserve necessary context and tool fixtures. Full-conversation regression should feed scripted user turns and newly generated assistant responses forward. Do not insert the golden assistant answer as the preceding response; that hides propagation failures. If testing a single turn against fixed historical context, label it as a distinct evaluation mode.

Start with scripted user turns. Autonomous user simulation is a separate later feature because it adds another source of nondeterminism.

## Changes and publication

Every case edit creates a new revision, including edits to unapproved candidates. `expected_revision` rejects stale writes. Approval requires at least one valid required check; publication pins an explicit map of the latest approved revisions and rejects stale selections. Named immutable releases are implemented; a separate dataset-grouping entity is not. Past runs/bundles retain their pins, with explicit CI upgrades rather than latest-dataset synchronization.

V2 test-bundle export selects an agent revision, mode, and judge and embeds their non-secret execution pins separately from unchanged canonical cases. It is not dataset-only download. Archived project/agent history and explicit exports remain readable. Legacy historical execution specs stay unavailable (`spec_hash=null`); the migrated agent mappings are `mapping_only`, not reconstructed evidence. SDK 0.2.0 retains v1 bundle support.

Held-out-set enforcement, real-data authorization, and privacy deletion/retention tooling remain gaps. Separate development and held-out material operationally before making credible improvement claims. Export only authorized sanitized material; current redaction is best-effort, not comprehensive DLP.
