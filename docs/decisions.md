# Current Design Decisions

Original baseline finalized on 2026-09-07; project/agent organization is implemented as of 2026-09-08. Delivered choices and remaining verification are distinguished below; local fake-transport tests do not establish live Foundry connectivity or production security.

| Area | Decision |
|---|---|
| Product | **GoldenLoop**, a Microsoft-first workbench exclusively for Hack for Evals: Excel/CSV import, agent chat, observable tool-call inspection, answer/tool feedback, golden datasets, and evaluation. |
| Organization | Delivered v2 project-scoped workflows and Agents controls. Projects own cases/releases and agents; agents own immutable revisions. Sessions/runs pin a revision; archive preserves history. See [project/agent guide](projects-and-agents.md). |
| Agent configuration | Delivered non-secret revision specs using `synthetic-customer` / `goldenloop-demo-agent==0.2.0` and operator `GOLDENLOOP_CONNECTION_BINDINGS` endpoint/auth/project allowlists. Credentials remain server-side; no per-project environment mutation. Readiness is configuration only, not connectivity. |
| Judge configuration | Independent global `GOLDENLOOP_JUDGE_*`, snapshotted per run/export; not an agent project binding. Explicit provider selection, separate API opt-in, no fake fallback. |
| Upgrade | Bootstrap `0002` on the same backed-up SQLite database. Canonical case/release hashes and historical evidence stay unchanged; case IDs remain globally unique. Legacy revision provenance is `mapping_only`, historical execution `spec_hash` null. Migrated mappings are mock-only in v2; new live execution needs a new revision. |
| Compatibility | V1 stays Synthetic Demo-only with legacy global agent settings. SDK 0.2.0 supports v1/v2 bundles; v2 export explicitly pins revision/spec/mode/judge. Queued evaluations pinned to unavailable SDK/agent 0.1.0 fail closed before invocation, not silently upgraded. |
| Scope boundary | Project switching and multiple logical agents are accepted; organizations/teams, shared authentication, arbitrary endpoint invocation, and additional framework adapters are not part of this increment. |
| Frontend | React, TypeScript, Vite, React Router, and TanStack Query. |
| Backend | Python, FastAPI, Pydantic, and Uvicorn; a single process with a bounded evaluation runner. |
| Database | Local SQLite with SQLAlchemy, aiosqlite, and Alembic; a separate database per developer and a single-instance initial demo, not a shared network database. |
| Development | A shared non-root Linux devcontainer with Python and Node.js; uv and npm with committed lockfiles. |
| SDK and agent | One shared Python evaluation SDK and one instrumented Microsoft Agent Framework Python demo agent; canonical case/result schemas and agent invocation separate from evaluation. |
| Models | Delivered adapter targets Azure OpenAI Chat Completions through Foundry. Broader native Microsoft/open-source offerings remain a goal, not a generic endpoint capability. Live extras require `openai>=3.8,<4` and `httpx>=0.28,<1`. |
| Data | Start with labeled synthetic scenarios; retain an authorized real-data import/review path and redact sensitive data before persistence, model evaluation, or export. |
| Golden releases | Imports and interaction feedback create editable candidates. Review is required before promotion to immutable releases; edits create new revisions and preserve lineage. |
| Evaluation | Deterministic content/tool checks plus AI-as-a-judge, with scripted multi-turn cases. Evaluate answers and tools separately; missing traces and evaluator failures must not count as passes. |
| CI/CD | Path 1A's asynchronous invocation/evaluation contract is local-only until shared authentication/hosting exists. Path 2's backend-free v2 tests include judge pins and local credential placeholders. Package publication is pending. Path 1B hosted submitted-output scoring is deferred. |
| Runtime limits | Global queue/concurrency, payload, deadline, and judge-check-count bounds; no per-project quotas or monetary caps. Standalone SDK runs do not inherit API queue/run limits or opt-in switches. |
| Documentation | Update the guide and project documents only on explicit user request; no automatic decision or session upkeep. |

## Required Configuration and Verification

- Dependencies and Agent Framework observable trace integration are pinned and covered by fake transports; actual deployment validation remains separate.
- The read-only synthetic customer sandbox is implemented. Define real-data permissions, sanitization, retention, and export governance before expanding it.
- Explicit revision approval is implemented; assign responsible golden-case reviewers.
- Configure the specified checks and approve judge rubrics and numeric thresholds; every required check must pass.
- Verify Foundry access/tool calling/judge structured output with an explicit budgeted run; binding registration is not a live probe. Verify full devcontainer rebuild persistence.
- Confirm hosting resources, SDK distribution, and the exact deadline for the three-person team.
