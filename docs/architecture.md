# GoldenLoop Architecture

Status: current design, not yet implemented. The stack, component boundaries, and local topology below are selected. Dependency versions and external integrations still require verification.

## 1. Stack and local topology

| Layer | Technology / responsibility |
|---|---|
| Frontend | React + TypeScript + Vite SPA, React Router, TanStack Query |
| Backend | Python + FastAPI, Pydantic request/response validation, Uvicorn |
| Evaluation SDK | Installable Python package with canonical schemas, checks, judge adapters, and CLI |
| Demo agent | Microsoft Agent Framework, Python integration; sandboxed synthetic tools |
| Database | Local SQLite, SQLAlchemy 2 + aiosqlite, Alembic migrations |
| Model access | Foundry-native deployments, independently configured for agent and judge |
| Development | One Linux devcontainer containing Python, Node.js, Git, and development tools |
| Package tools | uv for Python, npm for frontend; committed lockfiles and frozen installs |
| Tests | pytest for Python, Vitest/React Testing Library for UI, Playwright for E2E |

```text
Developer browser
  -> React/Vite :5173
       -> /api/v1 proxy -> FastAPI/Uvicorn :8000 (single process)
                            |-- SQLite /var/lib/goldenloop/goldenloop.db
                            |-- local uploads/artifacts under the same data root
                            |-- bounded evaluation runner -> Python evaluation SDK
                            |-- Agent Framework demo agent -> sandbox tools
                            `-- agent/judge model adapters -> Foundry (remote)
```

Use a modular monolith: frontend and backend are separate applications; the runner and demo agent run within the backend process behind explicit interfaces. The SDK is independently installable. This topology has no separate database server, queue service, or distributed worker.

## 2. React frontend

- Screens: import preview/mapping, chat with trace panel, candidate review/editor, golden releases, run results/comparison, and export.
- FastAPI owns validation, authorization, publication, and gate decisions; the UI displays them rather than reimplementing evaluation logic.
- Use REST under `/api/v1` for commands and queries. Generate TypeScript API types/client from FastAPI's OpenAPI contract to reduce drift.
- Streaming: POST a message/run command, receive an ID, then GET an SSE event stream for answer deltas, tool events, and progress. Use fetch-based streaming when bearer headers are needed; do not put credentials in stream URLs.
- Persist final messages and completed tool events, not every token as a database write. Event IDs and status/result endpoints allow reconnect recovery; the persisted record is authoritative, and interrupted token streams may be incomplete.
- Vite proxies `/api/v1` to `127.0.0.1:8000` inside the container, preserving the path. The browser uses relative URLs, including under forwarded devcontainer/Codespaces ports. Avoid broad CORS defaults.
- Model credentials and judge prompts containing sensitive context stay in the backend; never put secrets in frontend `VITE_*` variables. Render agent/tool content safely, without executing HTML or scripts.

## 3. FastAPI backend and run lifecycle

Organize routers and services by imports, chat/traces, feedback, datasets/releases, runs/results, and exports. Persistence sits behind repositories; adapters isolate Agent Framework and Foundry specifics from the evaluator core.

- Pydantic models define the API contract; SDK case/result models do not depend on ORM entities or FastAPI.
- Parse CSV with Python's CSV support and `.xlsx` with openpyxl. Bound file/row sizes and move blocking parsing off the async event loop.
- Use one database session per request or runner unit of work, never one shared session across async tasks. Do not hold transactions during model calls or streaming.
- `POST /api/v1/evaluation-runs` persists an idempotent run request and returns `202` with a run ID. A single lifespan-managed runner scans queued records with bounded model-call concurrency.
- Persist `queued`, `running`, `completed`, `failed`, `cancelled`, or `interrupted` execution status separately from gate status. On restart, mark previously running jobs interrupted rather than silently passing or replaying side effects; queued jobs can be picked up. Explicit reruns create traceable new attempts.
- Cancellation and deadlines are cooperative and recorded. Disconnecting the browser does not cancel an evaluation; the user issues an explicit cancellation request.
- FastAPI `BackgroundTasks` alone is not a durable job system. Persisted run state and startup reconciliation are required even for this simple runner. Exactly-once external execution is not promised.
- Run one active Uvicorn application process. Development reload may interrupt runs; reports must expose this. Extract a durable worker/queue when runtime or scale demands it, rather than enabling multiple workers against this runner design.

## 4. SQLite persistence

SQLite serves local development and the single-host demo. Each developer runs an isolated database seeded from versioned synthetic fixtures. Share schemas and test data through Git/import/export, never a live database file.

### Schema groups

| Tables / entities | Stored information |
|---|---|
| `imports`, `import_rows` | Source metadata, validation issues, candidate links |
| `cases`, `case_revisions` | Stable identities, immutable revised inputs/expectations, review status |
| `datasets`, `dataset_releases`, `release_cases` | Named datasets, content hashes, pinned case revisions |
| `chat_sessions`, `messages`, `tool_calls`, `feedback` | Observations, correlation IDs, call arguments/results/errors, reviewer corrections |
| `agent_configs`, `evaluator_configs` | Versioned configuration snapshots and secret references, never secret values |
| `evaluation_runs`, `case_results`, `check_results` | Execution lineage, observations/references, individual checks, gate results |
| `artifacts` | Managed relative paths, hashes, sizes, media types, provenance |

Use relational keys and constraints for ownership, versioning, and result references; validated JSON payloads for ordered turns, nested tool arguments, and evaluator settings. Index dataset/release references, run status, session/turn/call IDs, and timestamps. Publish a release transactionally; use revision checks to reject conflicting edits instead of overwriting another reviewer.

Imports and feedback create candidates, not ground truth. Review promotes approved case revisions into immutable golden releases. Edits create new revisions; past releases and results retain their pinned case, agent, evaluator, and judge lineage.

### Operational rules and limits

- Enable foreign-key enforcement per connection, a finite busy timeout, and WAL mode after verifying runtime support. WAL permits reader/writer overlap, **not multiple simultaneous writers**.
- Verify the SQLite library actually loaded by Python, not only the `sqlite3` CLI. Use a build containing the upstream WAL-reset fix (3.51.3+ or a documented patched/backported release) before enabling WAL; see sources below.
- Keep transactions short, batch completed trace writes, and use bounded retry for database lock contention. Never retry external tool side effects merely because persistence failed.
- Store the database and its WAL/SHM sidecars on local container storage backed by a named Docker volume. Do not use SMB/NFS/Azure Files or a synchronized host folder for the live WAL database.
- Persist data across devcontainer rebuilds; deleting the volume is destructive. Exclude databases, sidecars, uploads, and generated reports from Git. Commit only approved synthetic fixture files.
- Store larger uploads/exports as bounded files behind an artifact-store interface. Use generated paths and atomic writes, and account for missing/orphan files because filesystem and DB writes are not one transaction.
- Use SQLite's backup API or a clean shutdown/checkpoint backup procedure, not an arbitrary copy of the live `.db` alone.

### When to migrate

Move to a server database for multiple backend replicas, sustained write contention, distributed workers, or stronger operational requirements. Cloud services and the future database are not selected. SQLAlchemy/Alembic do not make migration automatic: test data types, constraints, JSON queries, migrations, and concurrency behavior. Do not deploy this SQLite topology unchanged to autoscaled containers or rely on ephemeral storage.

## 5. Shared devcontainer design

The devcontainer consists of a checked-in `.devcontainer/devcontainer.json`, Dockerfile, and setup script. It is not implemented yet.

- One non-root Linux container with pinned Python/Node runtime versions and build image, verified for Agent Framework compatibility. Install dependencies with `uv sync --frozen` and `npm ci`.
- Include Python debugger/linter tooling, Node tooling, SQLite inspection utilities, and Azure CLI for individual developer sign-in. Never bake credentials into image layers or setup commands.
- Mount the workspace source and a per-workspace named data volume at `/var/lib/goldenloop`, with permissions for the development user. Isolate Linux dependencies from host virtualenvs/node_modules.
- Forward ports `5173` (UI) and `8000` (API/OpenAPI), privately by default. Services may bind to `0.0.0.0` inside the container without publishing them to the public network.
- `postCreateCommand` installs locked dependencies. An explicit, idempotent bootstrap task applies migrations and optionally seeds synthetic data; it must never reset existing data automatically. Developer tasks launch API and Vite with reload.
- Include an `.env.example` containing configuration keys and placeholders only. Use ignored local environment files, injected secrets, or individual Azure credentials. Never share login caches in the data volume.
- Support a mock model/agent mode for deterministic tests and UI work without Foundry credentials; live calls require separately configured endpoints/deployments and access.
- Prerequisites: Docker plus VS Code Dev Containers (or compatible tooling); GitHub Codespaces is an optional environment to verify. Each teammate gets the same tools but their own DB and credentials.
- No Docker-in-Docker, privileged mode, Docker socket mount, or Compose is required by this topology.

## 6. Repository layout

```text
.devcontainer/                 # developer image and bootstrap
apps/
  web/                         # React + TypeScript + Vite
  api/                         # FastAPI services, ORM, Alembic migrations
packages/
  goldenloop_eval/             # installable Python evaluation SDK + CLI
  goldenloop_demo_agent/       # Agent Framework adapter and sandbox demo tools
examples/agent-tests/          # exported-bundle integration example
tests/e2e/                     # browser/API end-to-end scenarios
fixtures/synthetic/            # approved seeds and sample spreadsheets
docs/                         # existing design context; request-only updates
```

Backend imports the SDK and agent adapter; neither package imports backend routers or persistence. Keep SDK optional live-model dependencies separate so fixture-only tests need no Foundry credentials. Verify SDK packaging and installation in a clean CI environment before export is considered complete.

### First implementation slice and verification

1. Devcontainer, locked dependencies, minimal React/FastAPI skeleton, migration, and synthetic seed task.
2. Import a case and reload it from SQLite; verify persistence across container rebuilds.
3. Stream a demo conversation and tool trace, save feedback, publish a release.
4. Execute SDK checks from the backend and an exported pytest example with identical fixtures.

Verify fresh-clone bootstrap, repeated non-destructive bootstrap, concurrent chat/import/run writes, migration upgrades, interrupted-run handling, and UI/API error states. Use temporary databases for tests, never a developer's working DB. Add GitHub Actions for Python lint/tests, frontend typecheck/tests/build, and mock-backed E2E; live Foundry tests should be explicit budgeted runs.

## 7. Component boundaries

The backend handles authentication, persistence, curation, exports, and run orchestration. The SDK handles schemas, checks, judge invocation abstraction, result aggregation, and gate calculation. It must not depend on the UI, backend database, or workbench service.

Agent adapters execute inputs and normalize outputs/traces. Keep invocation separate from scoring so callers can evaluate recorded responses as well as live agents. Correlate trace events to conversation, turn, run, and call IDs; preserve parent/span relationships and trace completeness.

## 8. CI/CD paths

### Path 1A: Hosted invocation and evaluation

- `POST /api/v1/evaluation-runs`: select a dataset release, registered target, and check configuration; return `202` with a run ID.
- `GET /api/v1/evaluation-runs/{id}`: return execution status, gate status, check results, and report reference.
- `GET /api/v1/dataset-releases/{id}/export`: obtain an authorized portable bundle.

The workbench invokes the registered agent and evaluates its responses through the SDK. CI submits an asynchronous run and polls the result. Use cancellation, bounded concurrency, timeouts, retry rules, and idempotent submission as defined by the run lifecycle. CI fails on required-check failure or incomplete/error runs, not just HTTP errors.

For example, GitHub Actions requests release `v1` against an explicitly registered preview agent revision. The workbench sends scenario messages, captures answers/tool traces, and applies checks. It must test the intended build, not an unrelated older deployment.

Only invoke preconfigured authorized targets, not arbitrary user-provided URLs. This avoids using the backend as an unrestricted network proxy. Credentials remain server-side; machine callers need authenticated, scoped access.

### Path 2: Repository-local SDK tests

The CI test process invokes the agent and runs the same Python SDK without contacting the workbench backend.

Export:

- Versioned cases in canonical JSON/JSONL and any sanitized mock fixtures
- Manifest with schema, dataset release/hash, SDK version, evaluator/rubric versions, and thresholds
- A thin Python/pytest wrapper and an agent adapter stub
- Configuration example containing environment variable names, never credentials

Export data plus a small stable wrapper, not custom test code for each case. Judge/content checks live in the SDK. Export declarative checks only; never execute arbitrary code supplied in spreadsheet cells or feedback.

**Self-contained means independent of the workbench, not necessarily offline.** Live agent and LLM-judge checks still require model access, credentials, and budget. Deterministic checks on recorded/mock outputs can run offline. The SDK package must be installable in CI (distribution mechanism pending).

### Deferred: Path 1B hosted output scoring

Hosted scoring of CI-supplied responses/traces is out of scope; no submitted-output scoring endpoint is included in the current API. Invocation and scoring remain separate inside the SDK so a later API can score case-keyed observations against a pinned release without rerunning the agent. That path requires output and trace evidence, not a prompt alone, and redaction and upload authorization even when the agent runs privately.

## 9. SDK responsibilities

- Validate and load cases, expectations, observations, manifests, and results.
- Run deterministic evaluators and pluggable judge providers against normalized observations.
- Produce common pass/fail/error/skipped results with evidence and applicability reasons.
- Evaluate answers and tool behavior separately. Missing required traces and evaluator failures must not count as passes.
- Aggregate gates; emit machine JSON and a CI-friendly report such as JUnit.
- Support mocked judge/agent responses for testing SDK behavior itself.

Do not put curation workflow, user accounts, spreadsheet UI, or storage administration in the SDK. The CLI composes adapters and SDK calls for CI without duplicating evaluator logic.

## 10. Integration verification

The Python demo agent uses Microsoft Agent Framework and Foundry-native models. Agent and judge deployments are configured separately; model availability, tool calling, and structured output support must be verified for each deployment.

- Verify dependency/runtime compatibility, Agent Framework Python APIs, streaming and tool-trace hooks, and error/cancellation behavior.
- Verify Foundry deployment access and capabilities with budgeted integration tests.
- Assess existing Microsoft evaluation libraries and wrap compatible evaluators rather than duplicating them.
- Verify the loaded SQLite runtime and WAL patch level, devcontainer bootstrap, and standalone SDK installation. Design choices do not imply working integrations or available cloud resources.

## 11. Security and operational baseline

- Separate reviewer, executor, and read/export permissions, even if the demo has few users.
- Redact secrets/customer data before persistence and before sending judge context or exporting cases.
- Treat uploads, tool results, agent text, and judge output as untrusted data.
- Capture observable execution traces, not hidden chain-of-thought.
- Bound upload size, parsing complexity, run duration, trace size, judge spend, and concurrency.
- Use sandbox targets or mocks for side-effecting tools; replay must not send real emails or alter customer records.
- Define retention/deletion for raw traces and feedback independently of sanitized test releases.
- An explicit local-only synthetic demo mode may use a fixed development identity. It is not production authentication; require Entra ID and authorization before exposing a shared deployment or using customer data. Keep model credentials server-side in both modes.

## 12. Technical references

These references support design constraints, not proof of an implemented integration:

- [SQLite appropriate uses](https://www.sqlite.org/whentouse.html) — local storage fit and single-writer limits.
- [SQLite WAL](https://www.sqlite.org/wal.html) — same-host storage, backup considerations, and patched-runtime requirement.
- [Dev Container metadata reference](https://containers.dev/implementors/json_reference/) — mounts, lifecycle commands, users, and port forwarding.
- [FastAPI background tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/) — in-process tasks and limits relative to worker systems.
