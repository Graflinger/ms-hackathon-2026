# Current Design Decisions

Implementation baseline finalized on 2026-09-07. These are design decisions, not claims of implementation or verification.

| Area | Decision |
|---|---|
| Product | **GoldenLoop**, a Microsoft-first workbench exclusively for Hack for Evals: Excel/CSV import, agent chat, observable tool-call inspection, answer/tool feedback, golden datasets, and evaluation. |
| Frontend | React, TypeScript, Vite, React Router, and TanStack Query. |
| Backend | Python, FastAPI, Pydantic, and Uvicorn; a single process with a bounded evaluation runner. |
| Database | Local SQLite with SQLAlchemy, aiosqlite, and Alembic; a separate database per developer and a single-instance initial demo, not a shared network database. |
| Development | A shared non-root Linux devcontainer with Python and Node.js; uv and npm with committed lockfiles. |
| SDK and agent | One shared Python evaluation SDK and one instrumented Microsoft Agent Framework Python demo agent; canonical case/result schemas and agent invocation separate from evaluation. |
| Models | Foundry-native offerings, including OpenAI, Microsoft, and supported open-source models. |
| Data | Start with labeled synthetic scenarios; retain an authorized real-data import/review path and redact sensitive data before persistence, model evaluation, or export. |
| Golden releases | Imports and interaction feedback create editable candidates. Review is required before promotion to immutable releases; edits create new revisions and preserve lineage. |
| Evaluation | Deterministic content/tool checks plus AI-as-a-judge, with scripted multi-turn cases. Evaluate answers and tools separately; missing traces and evaluator failures must not count as passes. |
| CI/CD | Path 1A: hosted agent invocation/evaluation. Path 2: exported self-contained repository-local SDK tests. Defer Path 1B: hosted scoring of submitted outputs. |
| Documentation | Update the guide and project documents only on explicit user request; no automatic decision or session upkeep. |

## Required Configuration and Verification

- Pin and validate dependency versions, Microsoft Agent Framework integration, and observable trace hooks.
- Select the synthetic demo domain/tools; define permissions, sanitization, and export scope before using real data.
- Assign golden-case reviewers and configure the approval flow.
- Configure the specified checks and approve judge rubrics and numeric thresholds; every required check must pass.
- Verify Foundry deployments, model capabilities, and access.
- Confirm hosting resources, SDK distribution, and the exact deadline for the three-person team.
