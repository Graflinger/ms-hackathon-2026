# GoldenLoop

**From Agent Feedback to Golden Tests.**

GoldenLoop is a Microsoft-first evaluation workbench for **Hack for Evals: From AI Demos to Learning Systems**. Import scenarios, explore agent behavior, inspect observable tool calls, review expectations, publish immutable golden releases, and run the same evaluation logic in the workbench or in an agent repository.

**Current status:** working local, single-user, synthetic-data implementation. The default demonstration runs without Azure credentials. Optional live evaluation uses Microsoft Agent Framework with Azure OpenAI deployments available through Foundry; judging has its own Azure OpenAI configuration.

> This is not a shared production service. Keep both servers on localhost, use synthetic data only, and do not expose development mode through public forwarding. Real Foundry deployments require separate verification. The devcontainer is configured, but a complete container build/rebuild was not verified during initial implementation.

## Contents

- [What Is Implemented](#what-is-implemented)
- [Prerequisites](#prerequisites)
- [Local Setup: Windows PowerShell](#local-setup-windows-powershell)
- [Devcontainer Setup](#devcontainer-setup)
- [Environment and Secrets](#environment-and-secrets)
- [Connect Foundry](#connect-foundry)
- [First End-to-End Test](#first-end-to-end-test)
- [Import Scenarios](#import-scenarios)
- [Cases, Checks, and Releases](#cases-checks-and-releases)
- [Architecture](#architecture)
- [Evaluation and CI](#evaluation-and-ci)
- [Development and Tests](#development-and-tests)
- [Operations and Troubleshooting](#operations-and-troubleshooting)
- [Limitations and Design Context](#limitations-and-design-context)

## What Is Implemented

| Area | Current behavior |
| --- | --- |
| Import | UTF-8 CSV and XLSX preview, column mapping, provenance, validation, single-turn and grouped multi-turn candidates |
| Playground | Deterministic mock chat, observed messages, tool arguments/results/errors, answer/tool/missing-tool feedback |
| Review | Candidate authoring, canonical JSON editing, feedback review, explicit case approval, revision conflict detection |
| Releases | Immutable snapshots of explicitly selected approved revisions, content hashes, portable exports |
| Evaluation | Deterministic content/tool checks, optional live Azure judge, persisted asynchronous runs, cancellation, fail-closed gates |
| Results | Per-case evidence, execution status separate from gate status, same-release comparisons, available observation telemetry |
| Reuse | Installable Python SDK, CLI, JSON/JUnit reports, exported pytest wrapper and adapter |
| Development | uv workspace, React/Vite frontend, generated OpenAPI types, Alembic migrations, devcontainer, automated CI |

The synthetic sandbox contains `C-123` (Synthetic Alpine Bikes, gold tier) and `C-999` (Synthetic Cedar Cycles, silver tier). The **buggy** agent deliberately looks up the wrong customer; the **fixed** revision uses the requested ID. These are demonstration variants, not evidence of a production agent improvement.

## Prerequisites

Choose either native local development or the devcontainer. Both need a clone containing the application files described below.

| Tool | Version used by the project | Needed for |
| --- | --- | --- |
| Git | Current supported release | Repository checkout |
| Python | 3.12 | Native backend/SDK; uv can install it |
| uv | 0.10.12 | Python workspace and locked dependencies |
| Node.js / npm | Node 22.14.0 and its bundled npm | Frontend |
| Docker + Dev Containers tooling | Docker running Linux containers | Devcontainer option only |
| Azure CLI | Current supported release | Optional live-agent sign-in without an API key |

The Python packages declare `>=3.12`; Python 3.12 is the reproducible starting point used by CI. Dependency versions are recorded in [uv.lock](uv.lock) and [apps/web/package-lock.json](apps/web/package-lock.json). Install tools using their official distributions: [uv](https://docs.astral.sh/uv/getting-started/installation/), [Node.js](https://nodejs.org/en/download), [Dev Containers](https://code.visualstudio.com/docs/devcontainers/containers), and [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli).

Initial dependency installation needs network access. Once dependencies are installed, the mock demonstration needs no model service. UI fonts are bundled locally.

## Local Setup: Windows PowerShell

Run commands from the repository root unless a step explicitly says otherwise. Use `npm.cmd` in PowerShell to avoid restrictions on the npm PowerShell wrapper; no execution-policy change or virtual-environment activation is required.

### 1. Get the Repository

For a new checkout, run this from your chosen parent directory:

```powershell
git -c core.autocrlf=false clone https://github.com/Graflinger/ms-hackathon-2026.git goldenloop
```

Open a terminal in the resulting `goldenloop` directory. If you already have the workspace, use that checkout instead. The clone option preserves LF endings for Linux shell scripts without changing global Git settings.

### 2. Install Dependencies

```powershell
uv python install 3.12
uv sync --locked --all-packages --python 3.12
npm.cmd --prefix apps/web ci
```

Run the commands individually and stop if one fails. `--all-packages` is important: the root project is a development workspace; the API, SDK, and agent are separate members. `--locked` rejects an outdated lockfile rather than silently updating it.

For optional live agent/judge dependencies, also run:

```powershell
uv sync --locked --all-packages --extra live --python 3.12
```

Installing live dependencies does not enable live calls. A later base-only `uv sync` can remove unselected extras; repeat the live install when needed. The run commands below use `--no-sync` so they use the environment you explicitly installed.

### 3. Create Local Configuration

If `.env` does not already exist:

```powershell
Copy-Item -LiteralPath .env.example -Destination .env
```

Do not overwrite an existing `.env` containing your configuration. Keep these initial settings in the root `.env`:

```dotenv
GOLDENLOOP_LOCAL_DEMO=true
GOLDENLOOP_DATA_DIR=.goldenloop
GOLDENLOOP_ALLOW_LIVE_SYNTHETIC=false
GOLDENLOOP_ALLOW_LIVE_SYNTHETIC_JUDGE=false
```

Leave model credentials empty for now. `.env` is ignored by Git; `.env.example` contains placeholders only.

**The application does not automatically read `.env`.** The following commands explicitly load it through `uv --env-file`. Existing process environment variables take precedence over values from the file, so remove stale `$env:` overrides if changes appear to have no effect.

### 4. Bootstrap the Database

```powershell
uv run --no-sync --env-file .env python -m goldenloop_api.bootstrap --seed
```

This applies Alembic migrations and adds one **unapproved** synthetic candidate if it is absent. It does not reset data, overwrite an existing case, approve expectations, or publish a release. Omit `--seed` for an empty workbench. Startup requires bootstrap to have completed.

The command prints the actual database location and selected journal mode. With the configuration above and a root working directory, the database is `.goldenloop/goldenloop.db`.

### 5. Start the Backend

In the same root terminal:

```powershell
uv run --no-sync --env-file .env uvicorn goldenloop_api.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
```

Leave this terminal running. Do not start multiple workers. Reload is omitted deliberately: restarting the process interrupts active evaluations.

### 6. Start the Frontend

In a second terminal at the repository root:

```powershell
npm.cmd --prefix apps/web run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

| Address | Purpose |
| --- | --- |
| http://127.0.0.1:5173 | Workbench |
| http://127.0.0.1:8000/api/v1/health | Backend health |
| http://127.0.0.1:8000/api/v1/docs | Interactive API explorer |
| http://127.0.0.1:8000/api/v1/openapi.json | OpenAPI contract |

Check connectivity with:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/health
```

Stop each foreground server with `Ctrl+C`. After changing backend settings or secrets, restart the API from the configured terminal. Restart Vite only if its configuration changes.

For a local production-build preview instead of the development server:

```powershell
npm.cmd --prefix apps/web run build
npm.cmd --prefix apps/web run preview -- --host 127.0.0.1 --port 5173 --strictPort
```

Stop any existing frontend on that port first. Vite preview still needs the API and is not a production deployment server.

## Devcontainer Setup

The devcontainer provides a non-root Linux environment with Python 3.12, Node 22.14.0, uv, and Azure CLI. Docker must be running before opening it.

1. Open the checkout in VS Code.
2. Run **Dev Containers: Reopen in Container**.
3. Wait for `postCreateCommand` to install Python dependencies with `uv sync --frozen --all-packages` and frontend dependencies with `npm ci`.
4. In a container terminal at `/workspaces/goldenloop`, bootstrap explicitly:

```bash
bash .devcontainer/bootstrap.sh
```

Start the API in that terminal:

```bash
uv run --no-sync uvicorn goldenloop_api.main:app \
  --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
```

Start the UI in another container terminal:

```bash
npm --prefix apps/web run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Use the privately forwarded ports shown in VS Code. Public Codespaces/forwarded origins are not supported by the local-only security model.

**Storage:** separate named volumes hold `/var/lib/goldenloop`, `.venv`, and `apps/web/node_modules`. The database survives ordinary container rebuilds, and Linux dependencies are isolated from Windows dependencies. Removing the data volume deletes its contents; it is not a reset step.

The container supplies `GOLDENLOOP_LOCAL_DEMO=true` and `GOLDENLOOP_DATA_DIR=/var/lib/goldenloop`. Do not replace the latter with a relative workspace path when configuring secrets. For live development:

```bash
uv sync --locked --all-packages --extra live
```

Use an ignored `.env` with `uv run --no-sync --env-file .env ...`, or inject environment variables into the API terminal. The container already exports `GOLDENLOOP_ALLOW_LIVE_SYNTHETIC=false`, which takes precedence over `.env`. To let the file control that switch, run this in the API terminal before starting the backend:

```bash
unset GOLDENLOOP_ALLOW_LIVE_SYNTHETIC
uv run --no-sync --env-file .env uvicorn goldenloop_api.main:app \
  --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
```

Alternatively, explicitly `export GOLDENLOOP_ALLOW_LIVE_SYNTHETIC=true` in that terminal. Preserve the container data directory and enable only the live providers you intend to use. Run `az login` inside the container if using agent CLI authentication; do not share Azure login caches through the data volume.

## Environment and Secrets

The backend and SDK read process environment variables. `.env` loading in the examples is performed by uv, not FastAPI or Agent Framework. The frontend never needs a model credential.

| Variable | Default / requirement | Purpose |
| --- | --- | --- |
| `GOLDENLOOP_LOCAL_DEMO` | `false`; must be `true` locally | Enables the fixed local development identity |
| `GOLDENLOOP_DATA_DIR` | `.goldenloop` | Data location, relative to the process working directory unless absolute |
| `GOLDENLOOP_ALLOW_LIVE_SYNTHETIC` | `false` | Allows API runs to invoke the live synthetic agent |
| `GOLDENLOOP_ALLOW_LIVE_SYNTHETIC_JUDGE` | `false` | Allows API runs to use the live Azure judge |
| `AZURE_OPENAI_ENDPOINT` | Required for live agent | Azure OpenAI resource endpoint |
| `AZURE_OPENAI_CHAT_COMPLETION_MODEL` | Required for live agent | Deployment name, not necessarily the underlying model name |
| `AZURE_OPENAI_API_VERSION` | Required for live agent | API version supported by that endpoint/deployment |
| `AZURE_OPENAI_API_KEY` | Optional for agent | If absent, the agent uses Azure CLI credentials |
| `GOLDENLOOP_JUDGE_ENDPOINT` | Required for Azure judge | Independently configured Azure OpenAI resource endpoint |
| `GOLDENLOOP_JUDGE_DEPLOYMENT` | Required for Azure judge | Judge deployment name |
| `GOLDENLOOP_JUDGE_API_VERSION` | Required for Azure judge | Supported API version |
| `GOLDENLOOP_JUDGE_API_KEY` | Required for Azure judge | Current judge authentication is API-key based |
| `GOLDENLOOP_API_PROXY` | `http://127.0.0.1:8000` | Vite server-side API proxy override, not a model endpoint |
| `GOLDENLOOP_PYTHON` | Generator falls back to uv | Python executable used to generate/check frontend API types |

Boolean opt-ins accept `true`, case-insensitively; `1` and `yes` do not enable them. API concurrency, upload limits, and timeouts are currently code defaults in [config.py](apps/api/src/goldenloop_api/config.py), not additional environment variables.

### Secret Handling

- Keep credentials in an ignored local file, your process environment, or an approved secret-injection mechanism. `.env` is plaintext, not a secret vault.
- Never put keys in `VITE_*` variables, browser forms, case JSON, feedback, URLs, screenshots, logs, exported bundles, or Git.
- Never commit a populated `.env.example`. Check `git status` and `git diff` before committing.
- For CI, use the platform's secret store, restrict access, and keep live calls explicitly enabled and budgeted. Do not expose the local fixed-identity API publicly.
- Redaction is best-effort, not comprehensive PII detection or DLP. A synthetic label does not prove an upload is safe. Do not use real customer data in this version.
- Rotate any key that has been exposed; removing it from a file does not invalidate the credential.

To enter an agent key into a PowerShell process without putting the literal key in command history:

```powershell
$key = Read-Host "Azure OpenAI API key" -AsSecureString
$env:AZURE_OPENAI_API_KEY = [System.Net.NetworkCredential]::new("", $key).Password
```

The resulting process environment necessarily contains the plaintext credential. It is inherited by the API started from that terminal and is not persisted by this command. Use a separate prompt for a different judge key. Clear overrides with `Remove-Item Env:AZURE_OPENAI_API_KEY` when appropriate; this does not remove keys stored in `.env` or revoke them in Azure.

## Connect Foundry

### Endpoint and Model Requirements

The concrete live integration uses **Azure OpenAI Chat Completions**, with Microsoft Agent Framework for agent execution. It does not accept every model endpoint available in Foundry.

In Foundry, open your deployed model and inspect its endpoint/code example. Obtain the Azure OpenAI resource endpoint, deployment name, supported API version, and authentication details.

```text
Resource endpoint example: https://<resource>.openai.azure.com/
Deployment name example:  <your-deployment-name>
```

Use the resource endpoint expected by `AzureOpenAI`/`AsyncAzureOpenAI`, not a project URL ending in `/api/projects/...`, a complete `/chat/completions` URL, a connection string, or a generic serverless-model URL. Do not append an API path or put credentials into the URL. No deployment name or API version is assumed by the application.

The agent deployment needs tool/function calling. The judge deployment needs Chat Completions JSON-schema structured output and support for the supplied `temperature=0` setting. Azure permissions, network access, quotas, and deployment capabilities must be validated against your resource.

### Configure the Agent

Install live extras, then edit your ignored `.env`:

```dotenv
GOLDENLOOP_LOCAL_DEMO=true
GOLDENLOOP_ALLOW_LIVE_SYNTHETIC=true
GOLDENLOOP_ALLOW_LIVE_SYNTHETIC_JUDGE=false

AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
AZURE_OPENAI_CHAT_COMPLETION_MODEL=<agent-deployment-name>
AZURE_OPENAI_API_VERSION=<supported-api-version>
AZURE_OPENAI_API_KEY=<resource-api-key>
```

Keep your existing `GOLDENLOOP_DATA_DIR`. Replace the placeholders locally, then restart the API using the `--env-file .env` startup command.

Alternatively, leave the agent key empty/unset and authenticate in the same environment:

```powershell
az login
```

Select the correct tenant/subscription as needed and ensure your identity has permission to invoke the deployment. The agent uses `AzureCliCredential`, not an automatic managed-identity/default-credential chain.

**First connection test:** publish the deterministic customer-lookup case, create an evaluation with **Fixed**, execution mode **Live**, and judge **None**, then confirm the agent cost warning. Inspect the answer, trace, gate, and errors. This still invokes only the read-only synthetic customer tool, not a real CRM.

**Playground remains mock-only.** Switching an evaluation to Live does not make Playground chat live.

### Configure the Judge

Agent and judge deployments are configured independently. They may use the same resource, but must each support their required capabilities.

Add these settings to your ignored `.env` and restart the API:

```dotenv
GOLDENLOOP_ALLOW_LIVE_SYNTHETIC_JUDGE=true
GOLDENLOOP_JUDGE_ENDPOINT=https://<resource>.openai.azure.com/
GOLDENLOOP_JUDGE_DEPLOYMENT=<judge-deployment-name>
GOLDENLOOP_JUDGE_API_VERSION=<supported-api-version>
GOLDENLOOP_JUDGE_API_KEY=<resource-api-key>
```

The current judge requires an API key, even if the agent uses `az login`. Enabling a judge does not add judge checks to a case automatically.

In the candidate JSON editor, add a check to the `checks` array:

```json
{
  "kind": "judge",
  "required": true,
  "turn": 0,
  "config": {
    "rubric": "The answer identifies C-123 as Synthetic Alpine Bikes and correctly states its gold tier, consistent with the supplied tool evidence.",
    "threshold": 0.8
  }
}
```

This is an illustrative rubric/threshold, not a calibrated standard. Review it, approve the new case revision, and publish a new release. When launching its evaluation, select **Azure** judging and confirm the separate judge cost warning.

| Agent mode | Judge | Cloud calls |
| --- | --- | --- |
| Mock | None | None |
| Live | None | Agent only |
| Mock | Azure | Judge only |
| Live | Azure | Both |

Required judge checks without an explicitly selected, configured judge are rejected before queueing. Azure judging requires 1-20 judge checks across the release. There is no workbench mock judge and no invented fallback score. Provider errors, malformed results, refusals, and insufficient evidence remain visible and block required checks.

Live calls incur provider charges. The implementation has count, timeout, and concurrency limits, **not a monetary spend cap**. Low temperature does not guarantee identical judge results. Cloud deployment readiness and rubric calibration remain separate validation tasks.

## First End-to-End Test

### Review, Release, Fail, Fix

1. Open the workbench and select **Candidates**.
2. Review the seeded **Synthetic customer lookup** case. Its expectations require the requested customer ID in the answer and in the `lookup_customer` argument.
3. Enter an approval reason, confirm that you reviewed the expectations, and approve the current revision.
4. Open **Releases**, create a release, select the approved revision, and confirm publication.
5. Evaluate that release with **Buggy**, **Mock**, and judge **None**. Inspect the incorrect `C-999` call and the failed gate.
6. Evaluate the **same release** with **Fixed**, **Mock**, and judge **None**. Expect a passing gate.
7. Compare both runs and inspect per-check evidence rather than relying on the overall answer's plausibility.
8. Export the release using **Export test bundle**.
9. Edit the working case into a new revision. The old release and its past results retain the old expectations.

This demonstrates regression detection using controlled synthetic variants. It is not a calibrated quality benchmark or proof of real-customer coverage.

### Capture Feedback

1. Open **Playground**, create a **Buggy** session, and ask `Look up customer C-123`.
2. Expand `lookup_customer` in the observable trace and inspect its arguments and result.
3. Select **Review this call**, explain the wrong identifier, and optionally record a proposed correction.
4. In **Candidates > Interaction feedback**, review the signal and create an unreviewed candidate.
5. Author the expected behavior and actionable checks, then approve and publish deliberately.

Answer feedback and missing-tool feedback are also supported. Missing tools are attached to a turn because no observed call ID exists. Accepting feedback is not the same as approving a case. Candidate conversion preserves user-turn context and provenance; it does not silently convert a correction or failed answer into ground truth or overwrite the original trace.

## Import Scenarios

### Single-Turn CSV

A UI-friendly template is:

```csv
title,user,reference_answer,context,tags
Synthetic customer lookup,Look up customer C-123,Synthetic Alpine Bikes is a gold tier customer.,Synthetic read-only sandbox,"synthetic,customer-lookup"
```

Upload it in **Import**, preview the source, map canonical fields to uploaded columns, select a duplicate policy, and confirm import. Reference answers remain reviewer material; the importer does not automatically make them exact-match assertions. Add checks before approval.

Supported backend fields are `title`, `user`/`question`, `reference_answer`, `context`/`scenario`, `tags`, `id`/`case_id`, `scenario_id`, and `turn_index`. Choose one alias per field. Mapping direction is canonical field name to source-column name.

The [sample CSV](fixtures/synthetic/customer-lookup.csv) uses `question` and `scenario`, so map those manually to `user` and `context`. If you map its case ID after seeding, duplicate rejection is expected; choose the deliberate new-case policy to keep a separate imported case. Tags are comma-separated or a JSON string array; semicolons are not tag separators.

### Multi-Turn CSV

```csv
scenario_id,turn_index,title,user,reference_answer
customer-followup,0,Synthetic customer follow-up,Look up customer C-123,Synthetic Alpine Bikes is a gold tier customer.
customer-followup,1,,Which customer and tier did you just find?,C-123 is Synthetic Alpine Bikes with gold tier.
```

Map both `scenario_id` and `turn_index` using the additional canonical-field mapping controls. Rows for a scenario must be contiguous and already ordered `0,1,2,...`; the importer does not sort them. Conflicting shared metadata is rejected. The grouping ID is provenance, not automatically the canonical case ID.

**Indexing:** imported `turn_index` and canonical check `turn` are zero-based. UI labels such as "Turn 1" are one-based. Source row numbers include the header, so the first data row is row 2.

### Validation and Duplicate Rules

- CSV must be UTF-8, optionally with a BOM. Excel support is `.xlsx`, not `.xls` or `.xlsm`.
- Blank rows are skipped; headers must be nonempty and unique. Formula cells are flagged, not evaluated. Macros and external workbook links are rejected.
- CSV has no typed formula cells: any value whose left-trimmed text starts with `=`, `+`, or `@` is rejected as formula-like, even in an unmapped column. Ordinary text such as `@assistant look up C-123` is affected; CSV quoting does not bypass this restriction. Rephrase the source value before importing.
- Preview stores sanitized parsed content in SQLite before candidate commit; it does not retain raw upload bytes.
- Import is atomic: invalid rows block the commit. There is no inline row correction or partial import; correct the source and preview it again.
- `reject` rejects duplicate IDs or exact context/turn fingerprints, including duplicates within the import. This is not semantic similarity detection.
- `new` assigns fresh IDs to all imported cases while preserving incoming IDs in provenance. There is no merge/update-existing option yet.
- Nested tool expectations are authored in the workbench, not imported from spreadsheet cells.
- Defaults include 2 MiB per upload, 1,000 data rows, 100 columns, 10 workbook sheets, and 100 resulting cases per commit. XLSX archive complexity/expansion is also bounded.

## Cases, Checks, and Releases

### Canonical Case

The SDK's Pydantic models are the shared representation for the API, evaluator, and exports. For example:

```json
{
  "id": "customer-id-regression",
  "revision": 1,
  "title": "Use the requested synthetic customer ID",
  "tags": ["synthetic", "customer-lookup"],
  "source": {"type": "manual", "synthetic": true},
  "context": "Synthetic read-only customer sandbox.",
  "turns": [{"user": "Look up customer C-123", "reference_answer": null}],
  "checks": [
    {
      "id": "correct-customer",
      "kind": "tool_arguments",
      "required": true,
      "turn": 0,
      "config": {
        "tool": "lookup_customer",
        "path": "customer_id",
        "operator": "equals",
        "value": "C-123"
      }
    }
  ],
  "fixture_version": "synthetic-v1"
}
```

New case/check IDs can be generated by the backend when omitted. At least one required, actionable, valid check is needed for approval. Approval validates configuration; it does not prove a reference is correct or require the buggy agent to pass.

### Supported Checks

| Kind | Meaning |
| --- | --- |
| `content_contains` / `content_excludes` | Literal, case-sensitive required/forbidden content |
| `exact_match` | Exact text equality where wording truly matters |
| `json_schema` | Parse the answer as JSON and validate a supported bounded schema |
| `tool_required` / `tool_forbidden` | Expected tool names/alternatives and supported occurrence constraints |
| `tool_arguments` | Argument path equality, subset, type, schema, or inclusive range |
| `tool_order` | Required ordered subsequence, not an exact trace snapshot |
| `judge` | Rubric-based scoring with a reviewed per-check threshold |

Argument assertions apply to every matching call and require at least one match. A `turn` of `null` applies content checks independently to every answer; tool checks use calls across the selected turns. Future runs match tool constraints, not historical call IDs.

JSON Schema support is intentionally restricted: regex patterns, remote/recursive references, unsupported formats/keywords, and excessive complexity are rejected to avoid untrusted-check execution risks. See [schema.py](packages/goldenloop_eval/src/goldenloop_eval/schema.py) and [evaluator.py](packages/goldenloop_eval/src/goldenloop_eval/evaluator.py) for exact supported semantics.

### Revision Rules

Every edit creates a new candidate revision, even if the previous revision was not approved. Stale edits are rejected using `expected_revision`. Only the latest revision can be approved and selected for a new release.

Publication submits an explicit `expected_revisions` map. If a case changed after selection, publication fails with a conflict instead of silently selecting newer content. A release stores immutable snapshots and a canonical content hash. Later case edits never rewrite prior release content or evaluation expectations.

Observed messages/tool calls, reviewer expectations, and judge opinions stay separate. Immutability is enforced by application workflows; the SQLite file is not a tamper-proof audit system.

## Architecture

### Runtime

```text
Browser
  |
  v
React + TypeScript + Vite :5173
  | relative /api/v1 requests, server-side proxy
  v
FastAPI + Uvicorn :8000 (one process)
  |-- Import / chat / feedback / review / release endpoints
  |-- SQLite + Alembic migrations
  |-- Persisted queued commands + in-process runner
  |     |-- one active evaluation run, cases sequentially
  |     `-- one active chat command
  |-- goldenloop_demo_agent
  |     |-- deterministic mock
  |     `-- Microsoft Agent Framework -> Azure OpenAI
  |                `-- read-only synthetic lookup_customer
  `-- goldenloop_eval
        |-- canonical schemas, deterministic checks, gates
        `-- optional separately configured Azure OpenAI judge

Agent repository / exported pytest / SDK CLI
  `-- trusted invocation adapter + goldenloop_eval
      (no running workbench or backend database dependency)
```

This is a modular monolith, not a distributed job system. FastAPI owns persistence, curation, authorization boundaries, and orchestration. The SDK owns evaluation definitions and gate calculation. Invocation adapters return normalized observations; they do not determine whether the agent passes.

The current database uses relational keys plus JSON payloads for cases, import previews, conversations, feedback, and run results. There is no separate queue service, server database, artifact service, or general-purpose observability collector.

The browser uses TanStack Query to fetch/poll authoritative results. API SSE endpoints support persisted completed events and reconnect cursors; they do **not** provide token-by-token streaming. Observable function calls/results are retained, not hidden chain-of-thought.

### Process Flow

```text
CSV / XLSX ---------------------------> Candidate
Manual authoring ---------------------> Candidate
Chat -> observed answer/tool trace
     -> feedback -> proposed correction -> Candidate
                                           |
                                    reviewer authors checks
                                           |
                                    explicit approval
                                           |
                                 immutable golden release
                                           |
                       agent invocation -> normalized observation
                                           |
                              SDK content/tool/judge evaluation
                                           |
                          execution status + gate + evidence
                                           |
                       compare / fix agent / rerun same release
                                           |
                          export pinned repository-local tests
```

Multi-turn evaluation feeds scripted user messages and actual generated history forward. It never substitutes golden reference answers for prior agent responses. Cases use isolated synthetic state. Playground continuation currently uses a mock-specific history rule, not a live conversational adapter.

### Repository Layout

```text
.devcontainer/                    Linux development environment and bootstrap
.github/workflows/ci.yml          Python, frontend, standalone, browser checks
apps/api/src/goldenloop_api/      API, migrations, persistence, imports, runner
apps/web/src/                    React screens and handwritten fetch client
apps/web/src/generated/          TypeScript types generated from OpenAPI
packages/goldenloop_eval/        Installable SDK, evaluators, judge, CLI
packages/goldenloop_demo_agent/  Mock and Agent Framework demo adapters
fixtures/synthetic/              Clearly labeled candidate scenarios
examples/agent-tests/            Backend-independent SDK bundle tests
tests/e2e/                       Real HTTP workflow and exported replay
tests/browser/                   Production-build browser workflows
docs/                            Product and architecture design baseline
```

### API Surface

All routes below are under `/api/v1`. Use the running [API explorer](http://127.0.0.1:8000/api/v1/docs) for current request and response schemas.

| Resource | Main operations |
| --- | --- |
| `/health`, `/summary` | Health and workbench counts |
| `/imports/preview`, `/imports/{id}/commit` | Multipart preview and mapped candidate import |
| `/cases`, `/cases/{id}`, `/cases/{id}/approve` | List/create/read/edit/approve |
| `/dataset-releases`, `/dataset-releases/{id}` | Publish/list/read immutable snapshots |
| `/dataset-releases/{id}/export` | Download portable ZIP |
| `/chat-sessions`, `/chat-sessions/{id}/messages` | Create sessions and queue mock turns |
| `/feedback`, `/feedback/{id}/review`, `/feedback/{id}/candidate` | Capture/review/convert feedback |
| `/evaluation-runs`, `/evaluation-runs/{id}`, `/evaluation-runs/{id}/cancel` | Submit/poll/cancel runs |
| `/chat-sessions/{id}/events`, `/evaluation-runs/{id}/events` | Completed-event SSE with `Last-Event-ID` or `after` recovery |

## Evaluation and CI

### Execution Is Not the Gate

Run execution states are `queued`, `running`, `completed`, `failed`, `cancelled`, and `interrupted`. Gate outcomes are `pass`, `fail`, or `error`; check outcomes also include `skipped`.

- All selected cases must execute, and every required check must pass for a green gate.
- Required check failures produce `fail`; required errors/skips or incomplete execution produce `error`.
- Missing answers, mismatched observation identity, malformed traces, or tool/agent execution errors cannot be treated as successful execution.
- Missing required tool telemetry blocks tool checks. A content-only case does not automatically require a complete tool trace.
- Optional failures/errors remain visible but do not decide the gate or inflate a pass count.
- A run can be `completed` while its gate is `fail` or `error`. HTTP success and execution completion alone are not CI success.
- No averaged quality score can hide a required tool failure.

Queued requests and partial results are persisted. Startup marks previously running work interrupted rather than replaying side effects; queued work can be picked up. Browser disconnection does not cancel a run. Reruns should use a new idempotency key; an identical submission with the same key returns the existing run, while conflicting reuse returns 409.

### API Invocation Path

The workbench implements the asynchronous contract for invoking its built-in agent and evaluating the results. For example, from PowerShell with a real published release ID:

```powershell
$body = @{
    release_id = "<published-release-id>"
    agent_revision = "fixed"
    mode = "mock"
    judge = "none"
    idempotency_key = [guid]::NewGuid().ToString()
} | ConvertTo-Json

$run = Invoke-RestMethod -Method Post `
    -Uri http://127.0.0.1:8000/api/v1/evaluation-runs `
    -ContentType application/json -Body $body

Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/evaluation-runs/$($run.id)"
```

Poll to a terminal execution state and require `status=completed` **and** `gate=pass`; apply a polling deadline. The current CI example starts a private loopback API on its own runner. GitHub-hosted runners cannot reach a developer's localhost. Authenticated shared hosting and arbitrary agent-target registration are not implemented, so this is not yet a public hosted evaluation service.

### SDK Smoke Test

From the installed workspace, no API required:

```powershell
uv run --no-sync goldenloop-demo-agent evaluate fixtures/synthetic/cases.json --revision fixed --mode mock
```

Expected result: pass. Repeat with the deliberate regression:

```powershell
uv run --no-sync goldenloop-demo-agent evaluate fixtures/synthetic/cases.json --revision buggy --mode mock
```

Expected result: fail with exit code 1. The JSON fixture is explicitly candidate material; direct SDK execution does not imply human approval.

CLI exit codes are **0 = pass**, **1 = gate failure**, **2 = configuration/execution/evaluator error**. `--json` and `--junit` write reports; their parent directories must already exist. See `uv run --no-sync goldenloop-eval --help` for entry points.

The SDK also supports recorded-observation scoring. This evaluates supplied evidence, not a new agent invocation. There is no hosted submitted-output scoring endpoint.

### Exported Tests

A release ZIP contains:

```text
cases.json          Canonical pinned cases and check configuration
manifest.json       Schema/SDK version, release ID/hash, agent revision/mode
adapter.py          Trusted synthetic adapter; replace for your sandbox agent
test_release.py     Thin pytest wrapper around the shared SDK
requirements.txt    SDK/demo package versions and pytest requirement
.env.example        Configuration placeholders, no credentials
```

The initial export manifest uses `fixed` and `mock`; it does not copy the configuration of an inspected run. Self-contained means **independent of the workbench**, not bundled dependencies or necessarily offline execution. The repository does not yet publish SDK releases to a configured package registry, so do not assume `pip install -r requirements.txt` works on a fresh unrelated machine.

To try an extracted bundle with the already installed workspace environment, set these paths from the repository root:

```powershell
$Python = Join-Path (Get-Location).Path ".venv\Scripts\python.exe"
$Bundle = (Resolve-Path -LiteralPath "C:\path\to\extracted-bundle").Path

& $Python -m goldenloop_eval bundle $Bundle --adapter goldenloop_demo_agent:run_case `
    --json (Join-Path $Bundle "report.json") --junit (Join-Path $Bundle "junit.xml")
```

The API may be stopped. To run its pytest wrapper, keep `$Python` as the absolute workspace interpreter and open/run from the extracted bundle directory:

```powershell
& $Python -m pytest -q test_release.py
```

To install only SDK/demo/test dependencies into a separate locked environment, without the backend, use a new root PowerShell terminal:

```powershell
$env:UV_PROJECT_ENVIRONMENT = ".goldenloop/standalone-venv"
uv sync --locked --package goldenloop-demo-agent --extra test --no-default-groups --python 3.12
$env:GOLDENLOOP_ASSERT_NO_API = "true"
uv run --no-sync pytest -c examples/agent-tests/pytest.ini examples/agent-tests
```

Use that environment's Python for your exported bundle. Close the terminal afterward, or clear `UV_PROJECT_ENVIRONMENT` and `GOLDENLOOP_ASSERT_NO_API` before returning to normal development. This avoids replacing the normal workbench environment with a selected-package install.

Required judge checks fail without a judge. The generated pytest wrapper does not configure one; use the SDK CLI's `--judge azure` with the required environment/live dependencies, or deliberately extend the trusted wrapper. Standalone invocation does not enforce the API's `GOLDENLOOP_ALLOW_*` switches: the manifest/CLI mode and explicit judge selection control calls. Review these before execution. Never load arbitrary untrusted adapter code.

## Development and Tests

### Python and HTTP Integration

Install all extras to include framework-dependent tests; these use fake model transports, not live Azure calls:

```powershell
uv sync --locked --all-packages --all-extras --python 3.12
uv run --no-sync ruff check .
uv run --no-sync pytest
```

The default pytest suite includes SDK, agent, API, standalone-example, and real HTTP workflow tests. It uses temporary databases and does not require the manually started workbench. Browser tests are separate.

### Frontend and Contract

```powershell
npm.cmd --prefix apps/web ci
npm.cmd --prefix apps/web run typecheck
npm.cmd --prefix apps/web test
npm.cmd --prefix apps/web run build
```

For constrained machines, use `npm.cmd --prefix apps/web test -- --maxWorkers=1`. `test:watch` is available for interactive development.

After changing API models/routes, regenerate the TypeScript contract and review the resulting diff:

```powershell
$env:GOLDENLOOP_PYTHON = Join-Path (Get-Location).Path ".venv\Scripts\python.exe"
npm.cmd --prefix apps/web run generate:api
npm.cmd --prefix apps/web run check:api
```

The generator imports OpenAPI without starting a server and uses pinned `openapi-typescript@7.13.0` through `npm exec`. It may require network access or a populated npm cache. The generated types supplement the handwritten client; they do not replace backend runtime validation. On Linux, set `GOLDENLOOP_PYTHON` to the absolute `.venv/bin/python` path or let the generator use uv.

### Browser Tests

Build first: browser tests exercise Vite's **production preview**, not its development compiler.

```powershell
npm.cmd --prefix apps/web run build
uv run --no-sync playwright install chromium
uv run --no-sync pytest tests/browser
```

On Linux/devcontainer, use `uv run --no-sync playwright install --with-deps chromium` to install browser system dependencies too. Use `npm` instead of `npm.cmd` for Linux commands.

Browser fixtures start isolated loopback API/frontend processes and cover review/release/fail/fix/export, chat feedback, imports, and desktop/mobile routes. Screenshots and diagnostics are written under the OS temporary directory's `opencode/goldenloop-browser-artifacts` folder, not committed to the repository.

### CI and Verification Scope

[CI](.github/workflows/ci.yml) runs Python/lint tests, a backend-free SDK environment, frontend typecheck/tests/build, an OpenAPI drift check, and browser workflows on Ubuntu. It does not deploy the application, publish SDK packages, or run budgeted live Azure evaluations.

Initial implementation verification recorded 293 Python tests, 38 frontend tests, and five browser tests passing, plus build/lint/lock/contract checks. These are a point-in-time result, not a promise that an untested environment or live deployment works. Docker rebuild persistence and real Foundry calls still need external validation.

## Operations and Troubleshooting

### Database and Lifecycle

Use one local database per developer. Do not put a live database on SMB/NFS/Azure Files or synchronized folders. Container development uses its named volume; native development should use local disk.

Bootstrap enables foreign keys and selects WAL only when the SQLite library loaded by Python is at least `3.51.3`; older runtimes use `DELETE` journaling. Verify the actual runtime with:

```powershell
uv run --no-sync python -c "import sqlite3; print(sqlite3.sqlite_version)"
```

`journal=DELETE` is an intentional safety fallback, not a startup failure. The numeric guard does not detect older vendor backports. After changing Python/SQLite, stop the API and rerun bootstrap with the intended runtime. Do not enable WAL manually to bypass the guard.

Stop the API before maintenance/migration operations. For backups, use SQLite's backup API, or fully stop the service and use a controlled checkpoint/backup procedure. Never copy only a running `.db` while ignoring active WAL/SHM files. There is no built-in backup/restore or retention UI.

Default limits include a 120-second agent-invocation/chat deadline, 600-second run deadline, 100 pending runs, and 20 judge checks per Azure-judged run. Evaluation and judging occur after agent invocation, so a complete judged case can exceed 120 seconds. One run and one chat command can be active at once. An in-flight synchronous judge request may take up to its provider timeout to finish cleanup after cancellation; cancellation does not guarantee zero further provider cost.

### Common Problems

| Symptom | Check / action |
| --- | --- |
| `uv`, `node`, or `npm` not found | Install prerequisites and reopen the terminal. Prior temporary developer tool installations are not a portable setup. |
| PowerShell blocks `npm.ps1` | Use `npm.cmd`; do not weaken execution policy just for this project. |
| Module not found | Run `uv sync --locked --all-packages`; add `--extra live` for live features. Make sure the expected environment is selected. |
| `.env` changes are ignored | Start with `uv run --no-sync --env-file .env ...`, remove stale process overrides, and restart the API. |
| Local-demo opt-in required / 403 | Enable `GOLDENLOOP_LOCAL_DEMO=true`; use loopback host/origin. This mode deliberately rejects shared/public access. |
| Database not bootstrapped / schema mismatch | Stop the API and run the explicit bootstrap with the same data directory and environment. |
| Cases appear missing | Confirm the printed database path. Starting from a different working directory changes a relative data path. |
| Another process owns the database / port in use | Stop the specific existing API process. Do not add workers, delete the lock file, or reset the database to bypass ownership. |
| UI loads but API unavailable | Check API health and port 8000. Vite proxies `/api/v1`; model endpoints do not belong in its proxy setting. |
| 409 on edit/approval/publication | Reload and review current revisions; reselect deliberately. Do not silently replace the expected revision. |
| Import rejected | Check UTF-8, headers, selected sheet, formulas, mappings, zero-based scenario order, and duplicate policy. Correct and re-upload. |
| Approval blocked | Add at least one valid required check. A reference answer alone is not an actionable assertion. |
| Required judge configuration error | Add judge configuration, explicitly select Azure, and publish a case containing judge checks. Live agent selection alone is not enough. |
| Azure 401/403 | Check resource/key pairing or Azure CLI identity permissions, tenant, and network access. Do not print credentials for diagnosis. |
| Azure 404/deployment error | Use the correct resource endpoint and deployment name, not the model display name or project URL. |
| Azure 400/structured-output error | Verify API version, tool calling, JSON-schema output, and generation-setting support for that deployment. |
| Azure 429 | Check quota/rate limits and reduce live invocations. Do not repeatedly retry judgments until one passes. |
| Run completed but gate not green | Inspect required-check results and evidence. Completion only means the evaluation finished. |
| Chat interrupted/failed | Start a new session; incomplete sessions cannot accept new turns. |
| Export dependencies cannot be installed from an index | SDK publication is not configured; use the source-installed or separate locked environment described above. |
| Playwright executable missing | Install Chromium, and Linux system dependencies where needed, before browser tests. |
| Docker/devcontainer fails to start | Start Docker's Linux daemon, verify volume permissions and LF script endings; the initial implementation did not validate a full container rebuild. |

## Limitations and Design Context

This README documents the implementation, not the entire intended product. Important remaining gaps are:

- Shared Entra authentication, scoped machine callers, real role separation, hosted deployment, and arbitrary registered-agent targets.
- Live Playground conversations and token/incremental-span streaming; the current UI polls completed data.
- Real-customer authorization, comprehensive sensitive-data governance, retention/deletion tooling, and production hardening.
- Human judge overrides, calibration workflows, held-out-set enforcement, monetary budgets, and aggregate agent/judge cost reporting.
- Dataset grouping beyond named release snapshots, full revision-history browsing, import merge/update policies, and global semantic duplicate detection.
- Published SDK distribution and a turnkey judge-configured exported pytest wrapper.
- Server database/distributed workers, multi-replica execution, managed artifact storage, and general-purpose observability.

Hosted scoring of externally submitted outputs, autonomous user simulation, model training, and multiple framework/language adapters remain explicitly deferred. Recorded-output scoring is available inside the SDK only.

The [project guide](Agents.md) and older documents include planning-era status statements and a broader architecture baseline. In particular, statements that the repository contains no application code are now stale; planned token streaming, import merging, normalized artifact/configuration services, and shared authorization should not be interpreted as implemented. Those files are retained as design context rather than silently rewritten here.

| Document | Purpose |
| --- | --- |
| [Challenge brief](docs/challenge-brief.md) | Hack for Evals goals and evidence expectations |
| [Product scope](docs/product-scope.md) | Accepted workflows, demo story, and exclusions |
| [Architecture design](docs/architecture.md) | Intended component boundaries, topology, and migration triggers |
| [Dataset and feedback](docs/dataset-and-feedback.md) | Review principles and canonical-case requirements |
| [Evaluation design](docs/evaluation-design.md) | Judge/gate policy and reproducibility goals |
| [Decisions](docs/decisions.md) | Accepted baseline and pending choices |
| [Research and evidence](docs/research-and-evidence.md) | Integration validation and evidence gaps |
| [Project identity](docs/project-naming.md) | GoldenLoop name and pitch |

GoldenLoop's central rule is unchanged: **observed behavior, reviewed expectations, and judge opinions are different things. A plausible answer cannot compensate for a required tool failure, and missing evidence is not a pass.**
