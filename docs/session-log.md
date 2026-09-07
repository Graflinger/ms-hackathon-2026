# Session Log — Evals

## 2026-09-07 — Evals-only scope and product definition

- User selected Hack for Evals exclusively and requested removal of unrelated challenge material from active documentation.
- Defined an end-to-end UI/backend: Excel/CSV scenarios, interactive chat, tool-call inspection, and feedback on answers and tool parameters/behavior.
- Both imported scenarios and chat feedback should feed an editable golden evaluation dataset.
- Evaluation should support AI-as-a-judge and other checks, with external CI/CD access.
- User proposed hosted evaluation and exportable agent-repository tests, backed by shared SDK logic.
- Recommended reviewed candidates, immutable dataset releases, explicit tool expectations, deterministic checks, and trace-aware agent adapters.
- Recorded the distinction between observable execution traces and hidden model reasoning, and between platform-independent tests and completely offline tests.
- Added product, architecture, dataset, evaluation, and evidence documentation. No application implementation, commit, or push performed in this session.
- Repository is on `main`, with remote https://github.com/Graflinger/ms-hackathon-2026.git.

## 2026-09-07 — Framework, team, model access, and CI clarification

- User selected Microsoft Agent Framework for a new demo agent; no existing agent is available.
- Confirmed a three-person team with sufficient time for now; exact deadline remains open.
- User wants models native to Foundry, including OpenAI, Microsoft, and open-source offerings; specific deployments/capabilities still need verification.
- Start with synthetic data and retain the option for authorized real data later.
- User requested clarification of hosted CI choices. Documented who executes the agent versus who evaluates observations for hosted invocation, submitted-output scoring, and repository-local SDK tests.
- Recommended hosted invocation first to reuse the chat integration, without dropping repository-local tests or conflating submitted-output scoring with local evaluation. Priority remains proposed.

## 2026-09-07 — CI scope accepted and GoldenLoop selected

- User accepted Path 1A (hosted invocation/evaluation) and Path 2 (repository-local exported SDK tests) as the initial scope.
- Path 1B (hosted scoring of submitted outputs) is deferred; execution and evaluation remain separate internally.
- User selected **GoldenLoop** for hackathon registration. Retained its proposed registration pitch and removed alternative names. Registration and availability checks have not been performed here.
- User requested the move toward implementation and an end to automatic documentation updates. Future guide, decision-log, session-log, and topic-document changes require an explicit request.
