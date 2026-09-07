# Evaluation and CI Gate Design

Status: proposed evaluator semantics and validation requirements.

## Evaluate multiple layers separately

| Layer | Initial checks |
|---|---|
| Answer content | Required/forbidden content; exact match only where justified; JSON/schema validity |
| Tool behavior | Required/forbidden calls, argument assertions, call counts, explicit ordering constraints |
| Semantic quality | AI judge for correctness against evidence/reference criteria, completeness, relevance, and rubric-based behavior |
| Execution | Errors, timeouts, missing required traces, latency and usage/cost budgets when measurable |

A polished answer must not hide a critical tool failure. Make critical checks hard gates rather than averaging them away in a combined quality score. Groundedness checks require supplied source context; otherwise report not evaluable. Safety rubrics are useful but do not certify system safety.

## Judge design

- Pin rubric/prompt version and record model/deployment/version where available and generation settings.
- Request structured output: criterion score, concise justification, cited evidence, and uncertainty/insufficient-evidence status.
- Treat candidate text and tool output as untrusted quoted data, not judge instructions. Use no tools or privileges for a pure scoring judge.
- Validate output against a schema; malformed output and provider errors are evaluator errors, not passes.
- Calibrate against human-reviewed cases and measure disagreements and run-to-run variation.
- Keep human overrides attributable and separate from original judge results.

Using the same model family for agent and judge can introduce correlated errors; assess that risk. Low-temperature settings do not guarantee identical judgments. Shared code ensures consistent definitions, not identical stochastic outputs.

## Results and gate policy

Each check returns `pass`, `fail`, `error`, or `skipped`, plus reason, evidence, evaluator version, and optional score. The run separately reports completion status and aggregate gate status.

Proposed CI defaults:

- All selected cases and required checks must execute successfully.
- Any critical deterministic failure blocks the gate.
- Missing required telemetry, failed evaluators, unresolved required checks, or zero selected cases block the gate as incomplete/error.
- Apply an explicit threshold for judge-based criteria and report per-case failures, not only an average.
- Optional/skipped checks are visible and never inflate the denominator of passed checks.
- Retries are bounded and logged; do not retry flaky judgments until they pass.

Thresholds and criticality require user/domain-owner agreement before implementation. CLI exit codes must distinguish success from gate failure and execution/configuration error.

## Execution modes

- **Live end-to-end:** invoke the agent against sandbox tools, capture observations, then evaluate.
- **Recorded-output scoring:** evaluate submitted answers/traces; proves evaluator behavior on those observations, not a fresh agent run.
- **Mock/replay:** deterministic fixtures for tool responses and checks; not evidence of live integration reliability.

Label the mode and environment on every report. Reset scenario state between cases and use explicit fixture versions. Tests must not accidentally interact with production write-capable tools.

## Reproducibility and comparisons

Pin dataset release, agent code/configuration, tool/mock state, SDK, evaluator settings, judge prompt/model, and thresholds. Compare revisions against the same dataset and report coverage changes separately. Run repeated trials when claiming stochastic quality improvements; a tiny demo is not validation.

Track quality alongside latency and measured token usage where exposed. Cost estimates need a recorded price basis and must separate judge cost from agent cost. Unknown telemetry stays unknown.

## MVP verification checklist

- Import/edit/publish/export round trip preserves case meaning and provenance.
- Answer and tool feedback produces reviewed assertions without overwriting observations.
- Wrong tool parameters fail even if answer content is acceptable.
- A supported alternative tool trajectory does not fail solely because its trace differs.
- Hosted and exported deterministic checks match on identical fixtures.
- Multi-turn replay preserves actual generated history and isolates case state.
- Malformed judges, missing traces, empty selections, and timeouts cannot produce a green CI gate.
- Exported tests operate without the workbench backend; online dependencies are documented.
