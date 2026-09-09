# Projects and Agents

Status: **implemented in the local synthetic workbench**. Project-scoped v2 routes, registry controls, immutable specifications, migration `0002`, and SDK 0.2.0 portable exports are delivered. This is still a single-user localhost application, not shared authentication or arbitrary-agent hosting. Use the [README](../README.md) for startup and live setup commands.

## Ownership and Scope

The hierarchy is **Project -> Agent -> immutable Agent Revision**. Cases and golden releases belong to the project, not an individual agent, so compatible agents can use the same benchmark.

| Resource | Ownership / execution pins |
| --- | --- |
| Project | Stable ID, editable name/description, archive state |
| Agent | One project, editable display metadata, archive state |
| Agent revision | One agent/project, immutable number/label/specification/hash |
| Import, case, release | One project; releases pin approved case revisions |
| Session | One project and an agent revision pinned for its lifetime |
| Feedback, message command | Scope inherited from the session |
| Run | One project/release/revision, explicit mode and evaluator/judge snapshot |
| Events/results | Scope inherited from the session/run |

Request-scoped ORM lookups cover lists, details, counts, mutations, feedback conversion, exports, cancellation, and SSE recovery. Wrong-project record lookups return 404. Foreign keys and SQLite triggers enforce ownership agreement and immutable revision references. Workers execute already-pinned work across projects; browser switching does not cancel it.

Case IDs deliberately remain globally unique. Exact context/turn duplicate detection is project-local; repeated spreadsheet IDs can use the `new` import policy, retaining supplied IDs in provenance. Ownership is relational/API metadata, not a field added to canonical `Case`, so existing case/release hashes remain unchanged. There are no resource moves, shared cross-project releases, or hard-delete APIs.

Archiving blocks new work and metadata edits until unarchived. Queued/active work may finish, identical run retries can return the existing run, and cancellation, history reads, and explicit exports remain available. Archiving an agent also blocks further writes through its sessions/feedback. Projects organize data; the fixed local identity can access every project.

## Registry and UI

Create/select a project from the project chooser/sidebar. Its card displays the `project_id` needed for server binding configuration. Project settings support rename, description, archive, and unarchive. Stable `/projects/{project_id}/...` routes survive renaming; old top-level links target Synthetic Demo rather than the currently selected project.

In **Agents**, register a logical agent, then use **New revision** or **Create from this revision**. The form provides variant, non-secret instructions, live-mode opt-in, approved binding selection, deployment, and API version. Advanced JSON can restrict capabilities or choose live-only execution. Revision history shows IDs, hashes, specifications, and configuration readiness. Display metadata changes do not change specification hashes.

Playground, evaluations, and exports select an agent and a specific revision, never a mutable latest pointer. Playground requires mock mode, multi-turn support, and traces. Evaluations run the entire release after checking artifact, mode, fixture, multi-turn, and required tool-trace compatibility; incompatible cases are reported, not omitted. Comparisons require the same project/release and warn about differing execution/evaluator settings.

Project-prefixed query keys, project-bound form remounts, dirty-editor warnings, and captured-project callback guards keep project switching separate from active work. The UI polls persisted results; SSE contains completed events, not token streaming.

## Execution Specification

`AgentSpec.schema_version` is `"1"`, independently of API/manifest version `2`. The supported artifact is `goldenloop-demo-agent==0.2.0`, adapter `synthetic-customer`, variants `buggy`/`fixed`, fixture `synthetic-v1`, and tool contract `customer-lookup-v1`. Multiple logical agents reuse this adapter; arbitrary protocols/artifacts are rejected.

The specification contains `schema_version`, `adapter`, `artifact`, `variant`, `instructions`, `fixture_version`, `tool_contract`, `modes`, `supports_multi_turn`, `trace_available`, and optional `connection`. Mock execution stays deterministic; instructions affect live execution. Live mode requires a connection:

```json
{
  "endpoint": "https://example.openai.azure.com",
  "deployment": "your-agent-deployment",
  "api_version": "your-supported-api-version",
  "auth": "api_key",
  "binding": "foundry-agent"
}
```

This is metadata only, never a key/token. Endpoints normalize to credential-free HTTPS resource origins, with no API path, query, fragment, or user information. Execution changes create new revisions. Canonical SHA-256 hashing includes the validated specification with sorted modes; display metadata, timestamps, and credentials are excluded. A pinned deployment name does not freeze provider-side model updates.

### Operator Bindings

`GOLDENLOOP_CONNECTION_BINDINGS` is a JSON object, default `{}`, limited to 1 MiB. In a uv-loaded `.env`, quote the whole JSON value with single quotes:

```dotenv
GOLDENLOOP_CONNECTION_BINDINGS='{"foundry-agent":{"endpoint":"https://example.openai.azure.com","auth":"api_key","key_env":"GOLDENLOOP_AGENT_API_KEY","projects":["PROJECT_ID"]}}'
GOLDENLOOP_AGENT_API_KEY=
```

Replace `PROJECT_ID` with the actual ID from the created project's card (`synthetic-demo` for the migrated demo). Supply the credential separately in the API environment/ignored `.env`, not inside JSON. Restart the API with `uv run --no-sync --env-file .env ...` after configuration changes; process variables take precedence over the file.

- Binding entries accept exactly `endpoint`, `auth`, `key_env`, and `projects`.
- `projects` is an explicit list of permitted project IDs, not names or a wildcard.
- `auth="api_key"` requires `key_env` naming an operator-chosen environment variable; its nonblank value is required for execution.
- `auth="azure_cli"` uses `AzureCliCredential` with the Cognitive Services audience. Omit `key_env` and sign in with `az login` in the API's environment.
- Registration validates binding metadata and endpoint/auth/project agreement without requiring a key to be installed. Execution validates again and resolves credentials before invocation. Browser metadata exposes only binding ID, endpoint, auth, and `configured`, not key values or arbitrary environment lookup.

The endpoint/project/auth allowlist prevents a browser specification from redirecting an operator credential to an arbitrary destination. Explicit clients do not switch projects by mutating `os.environ`; agent/judge credentials are snapshotted in memory for execution, and HTTP redirects are disabled. This is not a general network-policy system or a claim of production security verification. Operators remain responsible for approved destinations and synthetic data.

**Readiness is configuration only.** An API-key binding checks key presence; an Azure CLI binding does not verify sign-in, permissions, deployment, quota, or network connectivity. Registration/export never performs a billable probe, and there is no dedicated connectivity-test endpoint/control. Verify live access with a separately opted-in evaluation against the read-only synthetic tool. Real Foundry calls remain unverified.

Agent live execution also requires installed live extras and `GOLDENLOOP_ALLOW_LIVE_SYNTHETIC=true`. `GOLDENLOOP_JUDGE_*` remains independent global configuration, not a project binding, and is snapshotted per run/export. API judging requires its own opt-in. Missing/changed configuration fails closed; history remains readable. Key rotation behind a binding does not change the revision hash; endpoint/deployment/auth/binding changes require a new revision.

## API and Compatibility

| Route | Purpose |
| --- | --- |
| `/api/v2/projects` | List/create projects |
| `/api/v2/projects/{project_id}` | Read/update metadata and archive state |
| `/api/v2/projects/{project_id}/agents` | List/register agents |
| `/api/v2/projects/{project_id}/agents/{agent_id}` | Read/update agent metadata/archive state |
| `/api/v2/projects/{project_id}/agents/{agent_id}/revisions` | List/create immutable revisions |
| `/api/v2/projects/{project_id}/agents/{agent_id}/revisions/{revision_id}` | Read a specification |
| `/api/v2/projects/{project_id}/connection-bindings` | Approved non-secret binding metadata |
| `/api/v2/projects/{project_id}/...` | Scoped summary, imports, cases, releases, chats, feedback, runs, exports, events |

Run submission uses `release_id`, `agent_revision_id`, `mode`, `judge` (`none`/`azure`), and `idempotency_key`. Uniqueness is `(project_id, idempotency_key)`; conflicting reuse returns 409. Export requires revision ID and mode, plus judge selection where needed. The OpenAPI document remains at `/api/v1/openapi.json` and includes both contracts.

`/api/v1` is retained as a Synthetic Demo compatibility surface with its old request shape. It cannot access other projects or v2-created sessions/runs. Legacy live execution still uses global `AZURE_OPENAI_*`; those variables are not defaults for new v2 agents. V1 export retains fixed/mock defaults. The new UI uses v2.

## Upgrade and Evidence

1. Stop the API. Back up with SQLite's backup API or a controlled shutdown/checkpoint procedure; never copy only a running `.db` while ignoring WAL/SHM files.
2. Install locked workspace dependencies, then run `uv run --no-sync --env-file .env python -m goldenloop_api.bootstrap` against the **same** `GOLDENLOOP_DATA_DIR`. No new database/service or destructive reset is needed.
3. Bootstrap applies `0002` after `0001`, creating Synthetic Demo (`synthetic-demo`), Synthetic Customer Lookup (`synthetic-customer-lookup`), and `synthetic-buggy`/`synthetic-fixed` revision mappings. These exist without `--seed`; optional seeding adds only the absent unapproved example. Repeated bootstrap does not duplicate them.
4. Restart the API and inspect old releases/results. Unknown legacy agent labels abort migration rather than defaulting to fixed. Destructive downgrade is unsupported; recovery uses the safe backup.

Migration preserves existing canonical payloads, release hashes, observations/results, timestamps, and historical event payloads. Legacy revision mappings are marked `legacy=true`, `spec_provenance="mapping_only"`; they do not reconstruct old live configuration. Historical execution metadata reports `spec_hash=null`. Registered revisions report `spec_provenance="registered"`.

**Migrated mappings are executable mock-only specifications in v2.** To run live from the new UI, create a new live revision with an approved binding, even if the old global agent settings still work through v1.

At startup, previously running work becomes interrupted. Queued evaluations pinned to SDK/agent 0.1.0 are marked `interrupted` with gate `error` before adapter/provider invocation, not silently executed under 0.2.0. Submit a new evaluation deliberately. Compatible queued evaluations are revalidated before execution; this version check concerns evaluation runs, not all pending mock chat commands.

## Portable Tests and Gaps

SDK 0.2.0 loads v1 and v2 manifests. V2 exports pin project/release IDs and hash, agent/revision/specification/hash, explicit mode, and judge metadata. The generated pytest wrapper calls the shared SDK with the selected judge; CI supplies local binding credentials and must match the pinned judge configuration. No workbench lookup is needed. Unsupported manifests/artifacts, hash mismatches, incompatible cases, or changed judge configuration fail closed. Hashes detect inconsistency, not authenticity of an untrusted bundle.

Live extras require `openai>=3.8,<4` and `httpx>=0.28,<1`; the demo additionally pins Agent Framework core 1.17.0/OpenAI integration 1.14.2. API count/deadline/concurrency limits remain global across projects, not per-project quotas or monetary caps. Standalone execution does not inherit API opt-in switches or queue/run limits; review manifest mode/judge before running it.

Delivered behavior has automated migration, scope, execution, bundle, UI, and browser regression tests; use the [verification commands](../README.md#development-and-tests), not a stale test count. Fake transports do not establish live connectivity or security assurance. Remaining work includes actual Foundry validation, full devcontainer rebuild verification, shared authentication/hosting, additional adapters, live Playground/streaming, SDK publication, and judge calibration/operational governance. No dedicated connectivity probe, dataset-only download, or project-local external-ID namespace is implemented.
