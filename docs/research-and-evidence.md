# Research and Evidence — Evals

## Evidence discipline

Distinguish **specified** requirements from **observed** results (with conditions and sample size), **externally supported** claims (with sources), and **validated** capabilities (tested against a representative validation plan). The architecture defines the stack; implementation and integration validation are pending. Official SQLite, FastAPI background-task, and Dev Container sources are linked in the architecture.

## Validation backlog

| Requirement / claim to validate | Required evidence |
|---|---|
| Hosted and exported checks behave consistently | Run identical fixtures/configurations through both paths; deterministic results must match. Separately measure judge variability. |
| Tool feedback detects wrong behavior | Fixtures for wrong tool, parameter, omission, forbidden call, and alternative valid trajectories. |
| Judge rubric agrees with domain reviewers | Independently labeled cases, disagreements, false-pass/false-fail rates, and repeated runs. |
| Feedback closes the regression loop | Preserve a failing agent revision, approved case, corrected revision, and comparable results. |
| Dataset covers customer delivery work | Permitted sanitized customer cases and a coverage inventory; synthetic examples clearly separated. |
| Traces can be obtained from the first agent | Verify actual framework hooks, async correlation, argument/result visibility, redaction, and export completeness. |
| CI gates fail safely | Deliberate timeouts, missing traces, malformed judge output, empty selections, and evaluator failures. |

## Integration verification

- Check official Microsoft evaluation tooling for reusable evaluators before building equivalents.
- Verify structured judge output support, deployment availability, auth, quotas, and pricing in the available Azure environment.
- Check instrumentation support and trace formats for the chosen agent; adapt OpenTelemetry where supported.
- Confirm spreadsheet parser behavior, file limits, and safe export handling.
- Verify Python SDK packaging/distribution and GitHub Actions integration.

## Minimum experiment record

Store dataset release/hash, case revisions, agent revision, tool/mock environment, SDK/evaluator versions, judge model/deployment and prompt version, run configuration, timestamps, statuses, quality scores, latency, usage/cost basis, reviewer decisions, repetitions, and limitations.
