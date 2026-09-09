# GoldenLoop — Project Guide

This is the concise entry point for humans and AI agents working on GoldenLoop, our InSpireD Hack for Evals project. A local synthetic implementation exists; prioritize building and testing over documenting every discussion.

## Documentation policy

- Update this guide and other project documentation only when explicitly requested by the user.
- Do not automatically create or update decision logs, session logs, or topic documents after discussions or implementation changes.
- These documents define the implementation baseline. If code or newer instructions conflict with them, flag the discrepancy rather than silently following stale requirements.
- When documentation updates are requested, keep them concise and focused exclusively on GoldenLoop and Evals.

## Mission and accepted scope

Build **GoldenLoop**, a Microsoft-first, end-to-end evaluation workbench with a UI and backend for **Hack for Evals — From AI Demos to Learning Systems**.

- Import scenarios and question/answer pairs from Excel and CSV.
- Chat with an agent to explore additional, multi-turn interactions.
- Inspect observable tool calls, parameters, results, and errors; give feedback on answers and individual calls.
- Turn imports and interaction feedback into an editable golden evaluation dataset.
- Run AI-as-a-judge and deterministic content/tool checks in the workbench.
- Support CI/CD through hosted evaluation APIs and self-contained test bundles in agent repositories.

## Confirmed starting conditions

- Backend: **Python + FastAPI**. Frontend: **React**. Provide a shared **devcontainer** setup for developers.
- Database: **local SQLite**, with a separate database per developer. See the architecture for concurrency limits and server-database migration triggers.
- Build a new demo agent using **Microsoft Agent Framework**; no existing agent is available.
- Team: three people; exact deadline not specified.
- Use models available natively through Foundry, including OpenAI, Microsoft, and open-source offerings where supported. Exact deployments and capabilities remain to be verified.
- Start with clearly labeled synthetic data; retain an import/review path for authorized real data later.
- Initial CI/CD scope: hosted agent invocation and evaluation (Path 1A), plus repository-local exported SDK tests (Path 2). Defer hosted scoring of submitted outputs (Path 1B), while keeping execution separate from evaluation internally.

## Implementation design

Use one canonical case/result schema and a shared evaluation SDK, called by both the backend and exported tests. Keep agent invocation adapters separate from checks. Imports and feedback create candidates; review promotes cases to immutable golden dataset releases. Editing creates a new revision rather than changing past results.

Use a Python evaluation SDK and one instrumented Microsoft Agent Framework Python demo agent. Support deterministic multi-turn scripts. Autonomous user simulation and general-purpose observability are outside the MVP.

Implemented: **Project → Agent → immutable Agent Revision**. The v2 API and UI scope curation, execution, events, and exports to projects; sessions/runs pin revisions. Bootstrap migration `0002` maps existing data into Synthetic Demo without changing canonical hashes/evidence. Multiple logical agents reuse `synthetic-customer` with `goldenloop-demo-agent==0.2.0`. Non-secret live specifications use operator-approved `GOLDENLOOP_CONNECTION_BINDINGS`; judge settings remain independent global configuration, snapshotted per run/export. See the project/agent guide for v1 compatibility and upgrade rules. Shared authentication and additional adapters are not implemented.

## Working principles

- Prefer Microsoft technologies where useful; avoid unnecessary services.
- Distinguish observed behavior, reviewer expectations, and judge opinions.
- Never treat a failed answer or an unreviewed correction as automatic ground truth.
- Evaluate answers and tool behavior separately; prefer deterministic checks for exact requirements.
- Expose observable execution traces, not hidden chain-of-thought.
- Preserve dataset, evaluator, judge, and agent version lineage for every run.
- Missing traces or evaluator failures must not silently count as passes.
- Redact sensitive data before persistence, model evaluation, or export; use sandboxed tools for replay.
- Label synthetic scenarios and unvalidated claims explicitly.
- Commit and push only when requested.

## Documentation map

- [Setup and usage](README.md) — current runnable setup, secrets, testing, and limitations
- [Projects and agents](docs/projects-and-agents.md) — delivered hierarchy, binding contract, scope, migration, and v1/v2 compatibility
- [Challenge brief](docs/challenge-brief.md) — Evals requirements and product fit
- [Product scope](docs/product-scope.md) — workflows, MVP, demo, and exclusions
- [Architecture](docs/architecture.md) — React, FastAPI, SQLite, devcontainer, shared SDK, and both CI/CD paths
- [Dataset and feedback](docs/dataset-and-feedback.md) — imports, cases, tool annotations, and golden releases
- [Evaluation design](docs/evaluation-design.md) — checks, judging, gates, and reproducibility
- [Decisions](docs/decisions.md) — accepted requirements and pending choices
- [Research and evidence](docs/research-and-evidence.md) — validation plan and technical uncertainties
- [Project identity](docs/project-naming.md) — selected name and registration pitch

## Required configuration and verification

1. Dependencies and observable trace integration are pinned and covered by fake-transport tests; verify actual Foundry tool calling and judge structured output separately.
2. The demo uses read-only synthetic customer lookup. Configure project-approved agent bindings and create a new live revision; migrated revisions are mock-only in v2. Binding readiness does not test connectivity.
3. Assign golden-case reviewers and approve judge rubrics and numeric thresholds. Required-check failures and incomplete evaluations block CI.
4. Confirm the delivery deadline, SDK distribution, and shared hosting resources.

## Repository state

- Remote: https://github.com/Graflinger/ms-hackathon-2026.git
- Branch: `main`
- Phase: local synthetic workbench with project/agent organization and SDK 0.2.0 v2 exports implemented
- Live Foundry deployment validation and full devcontainer rebuild verification remain outstanding
- Last updated: 2026-09-08
