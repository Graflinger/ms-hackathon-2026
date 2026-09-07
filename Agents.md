# GoldenLoop — Project Guide

This is the concise entry point for humans and AI agents working on GoldenLoop, our InSpireD Hack for Evals project. We are moving into implementation; prioritize building and testing over documenting every discussion.

## Documentation policy

- Update this guide and other project documentation only when explicitly requested by the user.
- Do not automatically create or update decision logs, session logs, or topic documents after discussions or implementation changes.
- Existing documents provide planning context; some designs remain proposals. If code or new instructions conflict with them, flag the discrepancy rather than silently treating stale plans as requirements.
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

- Build a new demo agent using **Microsoft Agent Framework**; no existing agent is available.
- Team: three people; sufficient time for now, exact deadline not specified.
- Use models available natively through Foundry, including OpenAI, Microsoft, and open-source offerings where supported. Exact deployments and capabilities remain to be verified.
- Start with clearly labeled synthetic data; retain an import/review path for authorized real data later.
- Initial CI/CD scope: hosted agent invocation and evaluation (Path 1A), plus repository-local exported SDK tests (Path 2). Defer hosted scoring of submitted outputs (Path 1B), while keeping execution separate from evaluation internally.

## Recommended design (not yet implementation decisions)

Use one canonical case/result schema and a shared evaluation SDK, called by both the backend and exported tests. Keep agent invocation adapters separate from checks. Imports and feedback create candidates; review promotes cases to immutable golden dataset releases. Editing creates a new revision rather than changing past results.

Start with one instrumented demo agent and one SDK language. Support deterministic multi-turn scripts before autonomous user simulation. Keep the first end-to-end path small; do not build a general-purpose observability platform.

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

- [Challenge brief](docs/challenge-brief.md) — Evals requirements and product fit
- [Product scope](docs/product-scope.md) — workflows, MVP, demo, and exclusions
- [Architecture and SDK](docs/architecture.md) — components and both CI/CD paths
- [Dataset and feedback](docs/dataset-and-feedback.md) — imports, cases, tool annotations, and golden releases
- [Evaluation design](docs/evaluation-design.md) — checks, judging, gates, and reproducibility
- [Decisions](docs/decisions.md) — accepted requirements and pending choices
- [Research and evidence](docs/research-and-evidence.md) — validation plan and technical uncertainties
- [Session log](docs/session-log.md) — Evals-focused discussion record
- [Project identity](docs/project-naming.md) — selected name and proposed registration pitch

## Main open questions

1. Which programming language and trace integration should we use with Microsoft Agent Framework?
2. Which synthetic demo domain/tools and Foundry model deployments should we select?
3. Who reviews golden cases, and which failures should block CI?
4. What are the exact deadline and backend/UI hosting resources? Three team members are confirmed.

## Repository state

- Remote: https://github.com/Graflinger/ms-hackathon-2026.git
- Branch: `main`
- Phase: transitioning into implementation; current repository contains planning documentation, not application code
- Last updated: 2026-09-07
