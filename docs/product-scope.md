# Product Scope — GoldenLoop

This document describes the local synthetic product and remaining scope. Project/agent organization is implemented; see the [README](../README.md) for runnable setup and verification limits. This is not a shared hosted service.

## Project and agent organization

Select a project before importing, reviewing, chatting, or running evaluations. Projects own cases/releases and registered agents; agents have immutable revisions. The sidebar selector and Agents screen support metadata/archive controls, revision creation/history, non-secret connection metadata, and approved binding readiness. Sessions/runs pin a revision; compatible agents can use the same project release. V2 enforces scope in backend lookups/storage, not just list filters. Existing data migrates to Synthetic Demo without rewriting hashes/evidence. See the [delivered contract](projects-and-agents.md) for setup and compatibility.

Multiple logical agents reuse `synthetic-customer` / `goldenloop-demo-agent==0.2.0`; registration does not add arbitrary endpoints/protocols. Live revisions require project-approved operator bindings, while judge configuration stays independent/global and snapshotted. Migrated revisions remain mock-only in v2; live execution needs a new revision. Readiness is configuration only, with no dedicated connectivity probe.

## Product thesis

Turn spreadsheet examples and interactive debugging into reviewed, versioned regression tests that run both in the workbench and in an agent's own repository. Cover answers and tool behavior in the same feedback-to-test workflow.

## Primary workflows

### 1. Import initial scenarios

Upload `.xlsx` or CSV, map columns, preview parsed rows and errors, then import candidates. Support simple question/reference-answer pairs first and scenario grouping with ordered turns. Track file/sheet/row provenance. Do not require nontechnical users to author nested JSON in spreadsheets.

### 2. Chat and inspect

Select a registered agent/revision and conduct a mock multi-turn conversation. The UI polls completed answers and observable tool arguments/results/errors, with timing where available. Annotate an answer, observed tool call, or missing call. Live Playground and token/incremental-span streaming are not implemented.

This requires an instrumented agent or an explicit trace-return contract. An answer-only endpoint cannot reveal internal tool calls. Execution traces are not hidden chain-of-thought. If traces are unavailable, label the limitation rather than infer calls from text.

### 3. Curate the golden dataset

Save an interaction and its feedback as a candidate case, including required conversation context. Review expectations, edit checks, and approve selected revisions into a golden release. Keep actual bad behavior separate from desired behavior. A thumbs-down with no correction is useful triage but insufficient reference truth.

### 4. Evaluate and diagnose

Choose a project release, registered revision, execution mode, and independent judge selection. Checks/rubrics are pinned in the release; the selected judge metadata is snapshotted. Compare same-release runs using per-check evidence and available observation telemetry. Missing telemetry remains unknown; aggregate agent/judge cost reporting, monetary budgets, and reusable evaluator-profile management remain gaps.

### 5. Reuse from CI/CD

- API path: request invocation/evaluation and consume a machine gate result. The asynchronous contract is delivered locally; shared hosting/authentication and hosted submitted-output scoring are not.
- Repository-local: explicitly select revision/mode/judge for a v2 bundle with non-secret specification and a judge-pinned pytest wrapper using SDK 0.2.0. No backend is required; live replay needs CI credentials matching the pins. V1 bundles remain supported; package publication is pending.

## MVP scope

- One workbench UI and backend; project-scoped agent registration and revision selection, initially using the existing instrumented demo adapter and safe tools. Multiple logical agents do not imply multiple framework adapters.
- Excel and CSV import with mapping and row validation.
- Single-turn cases and deterministic scripted multi-turn scenarios.
- Answer feedback and tool argument/behavior annotations.
- Candidate editing, explicit approval, and immutable golden releases.
- A small evaluator set: required/forbidden content, structured output, tool name/argument assertions, and one judge rubric.
- Python evaluation SDK, hosted execution API, and exported tests using the same core.
- Results view and one CI example showing a failed gate, a code fix, and a passing gate.

Defer autonomous user simulation, multiple agent-framework adapters, two-language SDKs, multi-tenant administration, model training, and a full observability platform. Production hardening is separate; access control, redaction, and safe tool execution remain required for any real data.

## Demo / acceptance story

1. Select a project, register an agent with buggy/fixed mock revisions (or use Synthetic Demo), and import a small spreadsheet. Correct validation errors in the source and re-upload; inline correction is not implemented.
2. Chat with the demo agent; observe a tool call using the wrong customer identifier despite a plausible final answer.
3. Annotate the parameter, verify the correction, and publish a golden case.
4. Run the buggy revision: the required tool assertion blocks the gate regardless of plausible answer content. Live judging is optional, separately configured, and not needed for this mock demo.
5. Select the fixed revision and rerun against the same release. These controlled variants are not proof of a production improvement.
6. Trigger the local API check from CI, then explicitly export and run the fixed/mock/no-judge bundle without the backend.
7. Edit a case into a new release; show that prior results retain their original expectations.

Synthetic identifiers and fixtures must be labeled. Customer coverage requires separately approved real scenarios.

## Remaining work

Actual Foundry tool-calling/structured-output validation, full devcontainer rebuild verification, shared authentication/hosting, SDK distribution, additional adapters, live Playground, dataset grouping beyond releases, import merging, calibration/human judge overrides, and production data/retention governance remain outside delivered behavior. Current resource limits are global count/deadline/concurrency bounds, not connectivity assurance, project quotas, or monetary caps.
