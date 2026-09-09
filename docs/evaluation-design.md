# Evaluation and CI Gate Design

This document defines evaluation semantics, CI gate policy, and validation requirements.

## Project and agent selection

The delivered [project/agent model](projects-and-agents.md) requires a project release and pinned revision from the same project. Preflight checks the supported artifact, declared mode, fixture, multi-turn capability, and trace capability for required tool checks. The supported spec fixes the tool contract to `customer-lookup-v1`. Incompatible cases are identified and rejected, never silently skipped; the entire pinned release runs.

Agent spec/hash and dataset content hash are separate from evaluator/judge lineage. Comparisons require the same project/release and show execution/evaluator differences. Migrated historical records retain old evidence and expose unavailable execution `spec_hash` as null; `mapping_only` revision specs do not reconstruct old live runs. New live v2 execution requires a new approved-binding revision, not the migrated mock-only mappings.

## Evaluate multiple layers separately

| Layer | Initial checks |
|---|---|
| Answer content | Required/forbidden content; exact match only where justified; JSON/schema validity |
| Tool behavior | Required/forbidden calls, argument assertions, call counts, explicit ordering constraints |
| Semantic quality | AI judge for correctness against evidence/reference criteria, completeness, relevance, and rubric-based behavior |
| Execution | Errors, timeouts, missing required evidence; available latency/usage telemetry, not monetary budget gates |

A polished answer must not hide a critical tool failure. Make critical checks hard gates rather than averaging them away in a combined quality score. Groundedness checks require supplied source context; otherwise report not evaluable. Safety rubrics are useful but do not certify system safety.

## Judge design

- Pin rubric/prompt version and record model/deployment/version where available and generation settings.
- Request structured output: criterion score, concise justification, cited evidence, and uncertainty/insufficient-evidence status.
- Treat candidate text and tool output as untrusted quoted data, not judge instructions. Use no tools or privileges for a pure scoring judge.
- Validate output against a schema; malformed output and provider errors are evaluator errors, not passes.
- Calibrate against human-reviewed cases and measure disagreements and run-to-run variation.
- Keep human overrides attributable and separate from original judge results.

Implemented: independent global `GOLDENLOOP_JUDGE_*` configuration, explicit `none`/`azure` selection, separate API live-judge opt-in, pinned rubric/prompt/settings, structured output validation, and fail-closed provider/evidence errors. Agent bindings do not configure judging. Human override and calibration workflows above remain requirements, not delivered UI. No mock judge is offered by the workbench.

Using the same model family for agent and judge can introduce correlated errors; assess that risk. Low-temperature settings do not guarantee identical judgments. Shared code ensures consistent definitions, not identical stochastic outputs.

## Results and gate policy

Each check returns `pass`, `fail`, `error`, or `skipped`, plus reason, evidence, evaluator version, and optional score. The run separately reports completion status and aggregate gate status.

CI gate policy:

- All selected cases must execute and every required check must pass.
- Any critical deterministic failure blocks the gate.
- Missing required telemetry, failed evaluators, unresolved required checks, or zero selected cases block the gate as incomplete/error.
- Apply an explicit threshold for judge-based criteria and report per-case failures, not only an average.
- Optional/skipped checks are visible and never inflate the denominator of passed checks.
- Retries are bounded and logged; do not retry flaky judgments until they pass.

Numeric thresholds and check criticality are versioned configuration approved by the domain owner. Missing required configuration blocks execution. CLI exit codes distinguish success from gate failure and execution/configuration error.

CLI exits are 0/pass, 1/gate failure, and 2/configuration/execution/evaluator error. API callers require both `status=completed` and `gate=pass`; a completed evaluation may still fail. Required judge checks with no selected/configured judge block submission; Azure selection requires 1-20 judge checks across the release, including optional checks in that count.

## Execution modes

- **Live end-to-end:** invoke the agent against sandbox tools, capture observations, then evaluate.
- **Recorded-output scoring:** the SDK evaluates supplied answers/traces; this tests those observations, not a fresh agent run. The hosted submitted-output scoring endpoint is deferred.
- **Mock/replay:** deterministic fixtures for tool responses and checks; not evidence of live integration reliability.

Label the mode and environment on every report. Reset scenario state between cases and use explicit fixture versions. Tests must not accidentally interact with production write-capable tools.

Playground remains mock-only. Registration/binding readiness/export make no model calls and do not verify live deployment connectivity. Live evaluation is the explicit potentially billable test; actual Foundry tool calling and judge structured output remain to be validated separately from fake-transport tests.

## Runtime and replay

API limits are global across projects: one active run plus one chat command, 100 queued/running records per queue, 120-second agent/chat invocation deadline, and 600-second run deadline. Providers use 60-second timeouts and zero retries. Judging follows agent invocation, so a judged case can exceed the invocation deadline. Cancellation may wait for a synchronous judge call to finish cleanup; neither cancellation nor count limits guarantees a monetary ceiling.

Run submission pins non-secret configuration. Workers revalidate artifact/spec/hash, release content, binding authorization, and judge settings before calls; credentials are snapshotted in memory for the run. Restart interrupts previously running work and queued evaluations pinned to unavailable SDK/agent versions (including 0.1.0), with gate `error` before invocation. It does not silently rerun old work using 0.2.0. Submit a new attempt deliberately.

V2 bundles explicitly select revision/specification/hash, mode, and judge metadata, using SDK/demo 0.2.0. The generated pytest wrapper selects the pinned judge; CLI replay needs `--judge azure` when Azure is pinned. CI resolves its own approved agent binding and must exactly match the pinned judge endpoint/deployment/API version. No backend lookup or exported secret is required. Export can read archived history without installed agent bindings/credentials; Azure judge export still needs global non-secret judge metadata.

SDK 0.2.0 retains v1 loading and canonical case hashes. Legacy v1 exports keep fixed/mock defaults and a wrapper without judge configuration. V2 uses the allowlisted revision adapter; mismatched pins/configuration fail closed. Live extras require `openai>=3.8,<4` and `httpx>=0.28,<1`. Standalone runs do not enforce API opt-in switches, queue limits, 600-second run deadlines, or the 20-check judge cap; they use a default 120-second invocation timeout. Review the manifest/selected judge before execution. Hashes check consistency, not bundle authenticity.

## Reproducibility and comparisons

Pin dataset release, agent code/configuration, tool/mock state, SDK, evaluator settings, judge prompt/model, and thresholds. Compare revisions against the same dataset and report coverage changes separately. Run repeated trials when claiming stochastic quality improvements; a tiny demo is not validation.

Track quality alongside latency and measured token usage where exposed. Cost estimates need a recorded price basis and must separate judge cost from agent cost. Unknown telemetry stays unknown.

## Verification scope

Automated suites cover the following with mocks, fake transports, temporary databases, and browser workflows; use README commands for current results, not stale counts. Real deployment calls, rubric calibration, shared hosting/authentication, and full devcontainer rebuild verification remain outstanding.

- Import/edit/publish/export round trip preserves case meaning and provenance.
- Answer and tool feedback produces reviewed assertions without overwriting observations.
- Wrong tool parameters fail even if answer content is acceptable.
- A supported alternative tool trajectory does not fail solely because its trace differs.
- Hosted and exported deterministic checks match on identical fixtures.
- Multi-turn replay preserves actual generated history and isolates case state.
- Malformed judges, missing traces, empty selections, and timeouts cannot produce a green CI gate.
- Exported tests operate without the workbench backend; online dependencies are documented.
