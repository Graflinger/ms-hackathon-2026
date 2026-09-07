# Proposed Architecture and Shared SDK

Status: design proposal, not an implemented API or finalized technology selection.

## Component boundaries

```text
Web UI: import / chat + traces / review / results
                         |
                    Backend API
                  /      |       \
           case store  run worker  agent adapter
                         |             |
                  Evaluation SDK   target agent + safe tools
                         |
              deterministic checks + judge adapter

Agent repository: case bundle + thin test wrapper
                         |
                 same Evaluation SDK
```

The backend handles authentication, persistence, curation, exports, and run orchestration. The SDK handles schemas, checks, judge invocation abstraction, result aggregation, and gate calculation. It must not depend on the UI, backend database, or workbench service.

Agent adapters execute inputs and normalize outputs/traces. Keep invocation separate from scoring so callers can evaluate recorded responses as well as live agents. Correlate trace events to conversation, turn, run, and call IDs; preserve parent/span relationships and trace completeness.

## Two CI/CD paths

### A. Hosted evaluation

Proposed API responsibilities (names illustrative):

- `POST /evaluation-runs`: select a dataset release, registered target, and check configuration; return a run ID.
- `GET /evaluation-runs/{id}`: return execution status, gate status, check results, and report reference.
- `POST /evaluation-results:score`: score supplied case-keyed responses and traces against a pinned release without invoking the agent.
- `GET /dataset-releases/{id}/export`: obtain an authorized portable bundle.

Confirmed initial scope: hosted invocation/evaluation (Path 1A) and repository-local SDK tests (Path 2). The submitted-output scoring endpoint (Path 1B) is deferred. Use asynchronous runs with cancellation, bounded concurrency, timeouts, retry rules, and idempotent submission. CI fails on required-check failure or incomplete/error runs, not just HTTP errors.

#### Clarification: who runs the agent versus who evaluates it?

| Mode | Runs the target agent | Executes evaluation checks | Needs workbench backend during CI? |
|---|---|---|---|
| Hosted invocation | Workbench worker calls a configured agent | Workbench using the SDK | Yes |
| Submitted-output scoring | CI runner or external application | Workbench using the SDK | Yes |
| Repository-local bundle | CI runner/test process | CI runner using the SDK | No |

Example hosted invocation: GitHub Actions requests dataset release `v1` against an explicitly registered preview agent revision, then polls the run result. The workbench sends scenario messages, captures answers/tool traces, and applies checks. It must test the intended build, not an unrelated older deployment.

Example submitted-output scoring: GitHub Actions runs the new agent build inside CI, collects case-keyed answers/traces using release `v1`, then submits those observations for hosted judging/checks. The workbench does not rerun the agent. A prompt alone cannot evaluate an external agent's behavior; its output and required trace evidence must be supplied.

Accepted approach: start with hosted invocation because the Microsoft Agent Framework demo/chat integration already needs that execution path, alongside repository-local exported SDK tests. Keep scoring independent inside the SDK; expose submitted-output scoring later. Private agent execution does not imply submitted data is private: redaction and permission are still required before upload.

Only invoke preconfigured authorized targets, not arbitrary user-provided URLs. This avoids using the backend as an unrestricted network proxy. Credentials remain server-side; machine callers need authenticated, scoped access.

### B. Tests inside the agent repository

Export:

- Versioned cases in canonical JSON/JSONL and any sanitized mock fixtures
- Manifest with schema, dataset release/hash, SDK version, evaluator/rubric versions, and thresholds
- A thin test wrapper for the selected language and an agent adapter stub
- Configuration example containing environment variable names, never credentials

Prefer data plus a small stable wrapper over generating separate custom test code for each case. Judge/content checks remain implemented once in the SDK. Export declarative checks only; never execute arbitrary code supplied in spreadsheet cells or feedback.

**Self-contained means independent of the workbench, not necessarily offline.** Live agent and LLM-judge checks still require model access, credentials, and budget. Deterministic checks on recorded/mock outputs can run offline. The SDK package must be installable in CI (distribution mechanism pending).

## SDK responsibilities

- Validate and load cases, expectations, observations, manifests, and results.
- Run deterministic evaluators and pluggable judge providers against normalized observations.
- Produce common pass/fail/error/skipped results with evidence and applicability reasons.
- Aggregate gates; emit machine JSON and a CI-friendly report such as JUnit.
- Support mocked judge/agent responses for testing SDK behavior itself.

Do not put curation workflow, user accounts, spreadsheet UI, or storage administration in the SDK. A lightweight CLI can compose adapters and SDK calls for CI without becoming a second evaluator implementation.

## Microsoft-first implementation options

Confirmed: build the demo agent with Microsoft Agent Framework and use Foundry-native model offerings, including OpenAI, Microsoft, and open-source models where available. Configure agent and judge deployments separately; do not assume every model supports the same tool calling or structured output features.

Proposed: a web UI, a backend/worker using the same language as the SDK, and a small relational store. Azure Container Apps is a hosting candidate; Entra ID for access; Azure Monitor/Application Insights and OpenTelemetry where useful for instrumentation. Add blob storage for larger traces/uploads only if needed.

Choose the backend/SDK language based on verified Microsoft Agent Framework support and team skills; this is not decided. Investigate existing Microsoft evaluation libraries after this choice; wrap suitable evaluators rather than reimplementing them. Exact model deployments, framework APIs, compatibility, and resource availability are not yet verified.

## Security and operational baseline

- Separate reviewer, executor, and read/export permissions, even if the demo has few users.
- Redact secrets/customer data before persistence and before sending judge context or exporting cases.
- Treat uploads, tool results, agent text, and judge output as untrusted data.
- Bound upload size, parsing complexity, run duration, trace size, judge spend, and concurrency.
- Use sandbox targets or mocks for side-effecting tools; replay must not send real emails or alter customer records.
- Define retention/deletion for raw traces and feedback independently of sanitized test releases.
