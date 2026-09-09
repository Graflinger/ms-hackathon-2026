# GoldenLoop Architecture

Status: delivered local synthetic architecture, with remaining operational work called out below. [Project and agent organization](projects-and-agents.md) is implemented. See the [README](../README.md) for setup, upgrade, and verification commands. Live Foundry calls and a full devcontainer rebuild remain unverified.

## Project and agent boundary

- The workbench hierarchy is Project -> Agent -> immutable Agent Revision. Cases and golden releases belong to the project and can evaluate multiple compatible agents.
- Persistence, queries, mutations, events, and exports are scoped server-side; sessions/runs pin revisions and runs snapshot evaluator/judge configuration. Projects do not replace shared authentication.
- The modular monolith retains one SQLite database per developer and the local-only boundary. No extra service is required.
- Migration maps existing data into Synthetic Demo without rewriting canonical case payloads, release hashes, or historical evidence. `buggy`/`fixed` remain internal selectors for migrated demo revisions.
- The UI uses v2 project-scoped APIs; v1 remains a Synthetic Demo compatibility surface. Request-scoped ORM criteria include indirect ownership and event streams; SQLite triggers enforce scope and revision immutability. Case IDs remain globally unique, with project-local scenario duplicate detection.
- Live revisions use non-secret specifications and operator-approved `GOLDENLOOP_CONNECTION_BINDINGS` endpoint/auth/project allowlists. Judge globals remain independent and are snapshotted per run/export. Binding readiness is configuration only, not a connectivity probe. See the [delivered contract](projects-and-agents.md).

## 1. Stack and local topology

| Layer | Technology / responsibility |
|---|---|
| Frontend | React + TypeScript + Vite SPA, React Router, TanStack Query |
| Backend | Python + FastAPI, Pydantic request/response validation, Uvicorn |
| Evaluation SDK | Installable Python package with canonical schemas, checks, judge adapters, and CLI |
| Demo agent | Microsoft Agent Framework, Python integration; sandboxed synthetic tools |
| Database | Local SQLite, SQLAlchemy 2 + aiosqlite, Alembic migrations |
| Model access | Azure OpenAI Chat Completions deployments through Foundry; agent and judge configured independently |
| Development | One Linux devcontainer containing Python, Node.js, Git, and development tools |
| Package tools | uv for Python, npm for frontend; committed lockfiles and frozen installs |
| Tests | pytest for Python, Vitest/React Testing Library for UI, Playwright for E2E |

```text
Developer browser
  -> React/Vite :5173
       -> /api/v2 + /api/v1 proxy -> FastAPI/Uvicorn :8000 (single process)
                            |-- SQLite /var/lib/goldenloop/goldenloop.db
                            |-- sanitized import previews and evidence in SQLite
                            |-- bounded evaluation runner -> Python evaluation SDK
                            |-- Agent Framework demo agent -> sandbox tools
                            `-- agent/judge model adapters -> Foundry (remote)
```

Use a modular monolith: frontend and backend are separate applications; the runner and demo agent run within the backend process behind explicit interfaces. The SDK is independently installable. This topology has no separate database server, queue service, or distributed worker.

## 2. React frontend

- Screens: project chooser/settings, Agents/revision history and creation, import preview/mapping, mock chat with trace panel, candidate review/editor, releases, run results/comparison, and configured export.
- FastAPI owns validation, authorization, publication, and gate decisions; the UI displays them rather than reimplementing evaluation logic.
- REST commands/queries use `/api/v2/projects/{project_id}`. Generated OpenAPI TypeScript types cover v1 and v2; the fetch client is handwritten. Project-prefixed query keys and form/callback guards prevent cross-project stale selections.
- The UI polls authoritative persisted results. SSE endpoints provide completed messages/tool events/status/results with event IDs and reconnect cursors, not answer-token or incremental-span streaming. Do not put credentials in stream URLs.
- Vite proxies `/api/v1` and `/api/v2` to `127.0.0.1:8000`, preserving paths. The browser uses relative URLs; only local/private forwarding is supported. Public Codespaces origins are not supported by the local-only security model.
- Model credentials and judge prompts containing sensitive context stay in the backend; never put secrets in frontend `VITE_*` variables. Render agent/tool content safely, without executing HTML or scripts.

## 3. FastAPI backend and run lifecycle

Routers/services cover registry, imports, chat/traces, feedback, releases, runs/results, and exports. Scoped SQLAlchemy sessions handle persistence; adapters isolate Agent Framework and Foundry specifics from the evaluator core.

- Pydantic models define the API contract; SDK case/result models do not depend on ORM entities or FastAPI.
- Parse CSV with Python's CSV support and `.xlsx` with openpyxl. Bound file/row sizes and move blocking parsing off the async event loop.
- Use one database session per request or runner unit of work, never one shared session across async tasks. Do not hold transactions during model calls or streaming.
- `POST /api/v2/projects/{project_id}/evaluation-runs` persists an idempotent run request with explicit revision/mode/judge and returns `202`. Uniqueness is project/key; the single lifespan-managed runner scans all projects under global capacity limits.
- Execution states are `queued`, `running`, `completed`, `failed`, `cancelled`, and `interrupted`, separate from gate status. Restart interrupts previously running work and queued evaluations pinned to unavailable executor versions, including SDK/agent 0.1.0, before invocation. Compatible queued runs are revalidated; reruns create new attempts.
- Cancellation and deadlines are cooperative and recorded. Disconnecting the browser does not cancel an evaluation; the user issues an explicit cancellation request.
- FastAPI `BackgroundTasks` alone is not a durable job system. Persisted run state and startup reconciliation are required even for this simple runner. Exactly-once external execution is not promised.
- Run one active Uvicorn application process. Development reload may interrupt runs; reports must expose this. Extract a durable worker/queue when runtime or scale demands it, rather than enabling multiple workers against this runner design.

## 4. SQLite persistence

SQLite serves local development and the single-host demo. Each developer runs an isolated database seeded from versioned synthetic fixtures. Share schemas and test data through Git/import/export, never a live database file.

### Schema groups

| Tables / entities | Stored information |
|---|---|
| `projects`, `agents`, `agent_revisions` | Ownership, metadata/archive state, immutable non-secret specs/hashes |
| `imports` | Sanitized parsed preview, source metadata, validation issues, commit state |
| `cases`, `case_revisions` | Stable identities, immutable revised inputs/expectations, review status |
| `dataset_releases`, `release_cases` | Named release snapshots, content hashes, pinned case revisions |
| `chat_sessions`, `message_commands`, `feedback` | Pinned revision, messages/tool calls in JSON, queued turns, reviewer signals |
| `evaluation_runs` | Pinned lineage, status/gate, observations and case/check results in JSON |
| `events` | Completed event payloads and stream/reconnect IDs |

There are no separate dataset, artifact, connection-profile, or evaluator-profile services/tables. Raw uploads are not retained; sanitized previews are persisted and ZIP exports are assembled in memory.

Relational keys and constraints enforce ownership/versioning; JSON payloads hold ordered turns, nested tool arguments, and evaluator settings. Indexes cover project ownership, run release/status, command session/status, feedback session, and event stream. Release publication is transactional, with revision checks rejecting conflicting edits rather than overwriting another reviewer.

Imports and feedback create candidates, not ground truth. Review promotes approved case revisions into immutable golden releases. Edits create new revisions; past releases and results retain their pinned case, agent, evaluator, and judge lineage.

### Operational rules and limits

- Enable foreign-key enforcement per connection, a finite busy timeout, and WAL mode after verifying runtime support. WAL permits reader/writer overlap, **not multiple simultaneous writers**.
- Verify the SQLite library actually loaded by Python, not only the CLI. Bootstrap selects WAL at version 3.51.3 or newer, otherwise DELETE journaling; it does not detect vendor backports. Do not bypass this guard manually.
- Keep transactions short, batch completed trace writes, and use bounded retry for database lock contention. Never retry external tool side effects merely because persistence failed.
- Store the database and its WAL/SHM sidecars on local container storage backed by a named Docker volume. Do not use SMB/NFS/Azure Files or a synchronized host folder for the live WAL database.
- Persist data across devcontainer rebuilds; deleting the volume is destructive. Exclude databases, sidecars, uploads, and generated reports from Git. Commit only approved synthetic fixture files.
- Larger uploads and managed file artifacts are deferred; current bounded previews/results are stored in SQLite and exports returned as ZIP responses.
- Use SQLite's backup API or a clean shutdown/checkpoint backup procedure, not an arbitrary copy of the live `.db` alone.

Upgrade by stopping the API, taking a safe backup, and running explicit bootstrap on the same data directory. Migration `0002` adds Synthetic Demo ownership without changing canonical evidence/hash payloads. Mapped revisions are mock-only, `mapping_only` provenance; historical execution `spec_hash` is null. New live revisions require approved bindings. No extra service or new database is needed; destructive downgrade is unsupported. See the [upgrade contract](projects-and-agents.md#upgrade-and-evidence).

Runtime defaults: one active run plus one chat command globally; 100 queued/running records per queue, 120-second agent/chat invocation deadline, 600-second API run deadline, and at most 20 judge checks for Azure judging. Provider requests use 60-second timeouts and zero retries. Limits do not constitute monetary spend caps or per-project quotas. API parsing/payload bounds are summarized in the README; standalone SDK execution does not inherit API queue/run limits or opt-in switches.

### When to migrate

Move to a server database for multiple backend replicas, sustained write contention, distributed workers, or stronger operational requirements. Cloud services and the future database are not selected. SQLAlchemy/Alembic do not make migration automatic: test data types, constraints, JSON queries, migrations, and concurrency behavior. Do not deploy this SQLite topology unchanged to autoscaled containers or rely on ephemeral storage.

## 5. Shared devcontainer design

The devcontainer configuration and setup scripts are checked in; full container build/rebuild verification remains outstanding. The following describes the design; use the README for current commands and local-only networking restrictions.

- One non-root Linux container with pinned Python/Node runtime versions and build image. Dependency/fake-transport tests cover Agent Framework integration; the full container remains to be verified. Install with `uv sync --frozen --all-packages` and `npm ci`.
- Include Python debugger/linter tooling, Node tooling, SQLite inspection utilities, and Azure CLI for individual developer sign-in. Never bake credentials into image layers or setup commands.
- Mount the workspace source and a per-workspace named data volume at `/var/lib/goldenloop`, with permissions for the development user. Isolate Linux dependencies from host virtualenvs/node_modules.
- Forward ports `5173` (UI) and `8000` (API/OpenAPI) privately. Run services on `127.0.0.1` under the current local-only boundary.
- `postCreateCommand` installs locked dependencies. Explicit idempotent bootstrap applies migrations and optionally seeds synthetic data, without resetting it. Start servers manually as in the README; API reload is omitted to avoid interrupting runs.
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
tests/e2e/                     # HTTP workflows and exported replay
tests/browser/                 # production-preview browser workflows
fixtures/synthetic/            # unapproved candidate seeds and sample spreadsheets
docs/                         # existing design context; request-only updates
```

Backend imports the SDK and agent adapter; neither package imports backend routers or persistence. Optional live dependencies are separate so fixture-only tests need no Foundry credentials. CI tests a backend-free SDK environment; publication to a package registry remains pending.

### Verification scope

Automated tests cover bootstrap/migration rollback and evidence preservation, project scope, concurrent writes, interrupted-run handling, fake transports, API/SDK parity, and UI/browser workflows using temporary databases. GitHub Actions runs Python lint/tests, backend-free SDK tests, frontend typecheck/tests/build, OpenAPI drift checks, and mock browser tests. Live Foundry validation and container rebuild persistence require separate checks; no fixed test count is a substitute for current results.

## 7. Component boundaries

The backend handles authentication, persistence, curation, exports, and run orchestration. The SDK handles schemas, checks, judge invocation abstraction, result aggregation, and gate calculation. It must not depend on the UI, backend database, or workbench service.

Agent adapters execute inputs and normalize outputs/traces. Keep invocation separate from scoring so callers can evaluate recorded responses as well as live agents. Correlate trace events to conversation, turn, run, and call IDs; preserve parent/span relationships and trace completeness.

## 8. CI/CD paths

### Path 1A: Hosted invocation and evaluation

- `POST /api/v2/projects/{project_id}/evaluation-runs`: select a release, immutable revision, mode, and judge; return `202` with a run ID.
- `GET /api/v2/projects/{project_id}/evaluation-runs/{id}`: return execution/gate status, lineage, observations, and check results.
- `GET /api/v2/projects/{project_id}/dataset-releases/{id}/export`: explicitly select revision/mode/judge for a portable bundle.

The workbench invokes the registered agent and evaluates its responses through the SDK. CI submits an asynchronous run and polls the result. Use cancellation, bounded concurrency, timeouts, retry rules, and idempotent submission as defined by the run lifecycle. CI fails on required-check failure or incomplete/error runs, not just HTTP errors.

The CI HTTP workflow selects a project release and demo revision, captures answers/tool traces, and applies checks. Supporting an arbitrary external preview build requires an additional trusted adapter, not merely a new display name or URL.

Only the supported adapter with operator-approved endpoint/auth/project bindings is available, not arbitrary user URLs/protocols. Credentials remain server-side. The asynchronous contract runs locally; authenticated shared hosting and scoped machine callers are not implemented. CI currently starts its own private loopback API.

### Path 2: Repository-local SDK tests

The CI test process invokes the agent and runs the same Python SDK without contacting the workbench backend.

Export:

- Canonical `cases.json` with pinned checks/rubrics/thresholds
- V2 manifest with project/release/hash, SDK 0.2.0, revision/specification/hash, artifact, mode, and judge metadata
- Thin pytest wrapper calling the supported revision adapter through the SDK, with explicit judge selection
- Requirements and configuration example containing binding metadata/environment names, never credentials

SDK 0.2.0 retains v1 bundle loading; only legacy v1 exports keep the fixed/mock default and replaceable adapter stub. V2 replay verifies pins and local binding/judge configuration without contacting the backend. Live extras require `openai>=3.8,<4` and `httpx>=0.28,<1`. Export itself makes no model calls.

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

- Permission hooks distinguish reviewer, executor, and read/export operations, but the fixed local identity has all permissions. Real role separation/shared authentication is not implemented.
- Redact secrets/customer data before persistence and before sending judge context or exporting cases.
- Treat uploads, tool results, agent text, and judge output as untrusted data.
- Capture observable execution traces, not hidden chain-of-thought.
- Upload size, parsing complexity, run duration, trace/result sizes, judge-check count, and concurrency are bounded. Monetary judge/agent budgets are not implemented.
- Use sandbox targets or mocks for side-effecting tools; replay must not send real emails or alter customer records.
- Retention/deletion policies and tooling remain required before real data; no such management UI is delivered.
- An explicit local-only synthetic demo mode may use a fixed development identity. It is not production authentication; require Entra ID and authorization before exposing a shared deployment or using customer data. Keep model credentials server-side in both modes.

## 12. Technical references

These references support design constraints, not proof of an implemented integration:

- [SQLite appropriate uses](https://www.sqlite.org/whentouse.html) — local storage fit and single-writer limits.
- [SQLite WAL](https://www.sqlite.org/wal.html) — same-host storage, backup considerations, and patched-runtime requirement.
- [Dev Container metadata reference](https://containers.dev/implementors/json_reference/) — mounts, lifecycle commands, users, and port forwarding.
- [FastAPI background tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/) — in-process tasks and limits relative to worker systems.
