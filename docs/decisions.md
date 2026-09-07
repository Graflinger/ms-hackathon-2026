# Decision Log — Evals

Active documentation is scoped exclusively to Evals at the user's request. Prior material remains in existing Git history; history has not been rewritten.

| Date | Status | Decision / requirement | Rationale |
|---|---|---|---|
| 2026-08-25 | Accepted, maintenance policy revised below | Use a concise `Agents.md` and linked detailed documents for project context. | Continuity across sessions; automatic maintenance is no longer required. |
| 2026-08-25 | Accepted | Prefer Microsoft technologies where useful. | Internal Microsoft hackathon. |
| 2026-09-07 | Accepted | Focus exclusively on Hack for Evals. | Explicit user selection. |
| 2026-09-07 | Accepted | Build an end-to-end UI and backend with Excel/CSV import, chat, tool inspection, answer/tool feedback, and an editable golden dataset. | User-defined product goal. |
| 2026-09-07 | Accepted | Support AI-as-a-judge and other checks, accessible to external CI/CD. | Shared interactive and automated evaluation. |
| 2026-09-07 | Accepted | Offer hosted evaluation and exported self-contained agent-repository tests using shared SDK logic. | Avoid separate internal and external testing implementations; user confirmed the recommended paths. |
| 2026-09-07 | Proposed | Require candidate review before publishing immutable golden releases. | Keep bad observations and unreviewed feedback out of reference expectations. |
| 2026-09-07 | Proposed | Start with one SDK language, one instrumented agent, and scripted multi-turn cases. | Keep the end-to-end build feasible. |
| 2026-09-07 | Accepted | Implement a new demo agent with Microsoft Agent Framework. | No existing target agent is available; framework selected by user. |
| 2026-09-07 | Accepted | Use Foundry-native model offerings; do not restrict the design to OpenAI alone. | User includes OpenAI, Microsoft, and open-source models available through Foundry. |
| 2026-09-07 | Accepted | Begin with synthetic scenarios and preserve a path to authorized real data. | Immediate dataset availability without requiring customer data access. |
| 2026-09-07 | Confirmed context | Three-person team, sufficient time for now. | Exact deadline and deployment resources still unspecified. |
| 2026-09-07 | Accepted | Implement hosted invocation/evaluation (Path 1A) and repository-local SDK tests (Path 2) first. Defer hosted submitted-output scoring (Path 1B), preserving the internal execution/scoring separation. | User accepted the recommendation after clarification; reuses chat integration and demonstrates both centralized and portable evaluation. |
| 2026-09-07 | Accepted | Name the project **GoldenLoop**. | User selected the registration name; alternatives removed from active documentation. |
| 2026-09-07 | Accepted | Update project documentation only on explicit user request; stop automatic discussion/session documentation. | Moving into implementation with a focus on building and testing. |

## Pending decisions

- SDK/backend language, Microsoft Agent Framework integration contract, and trace availability
- Synthetic demo domain/tools; later real-data permissions, sanitization, and export scope
- Golden-case reviewer and approval flow
- Initial checks, judge rubric, and CI thresholds
- Specific Foundry deployments/capabilities, hosting stack, SDK distribution, and deadline
