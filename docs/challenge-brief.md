# Challenge Brief — Hack for Evals

## Source and context

Based on the user-supplied InSpireD (ISD) brief: **Hack for Evals — From AI Demos to Learning Systems**. Motto: **If we can't measure it, we don't ship it.** Sponsors named in the brief: Edwina Fitzmaurice and Sarah Mocke. This is an internal Microsoft hackathon.

The challenge shifts evaluation from the model alone to the complete AI workflow: correct, useful, safe, repeatable, and economically viable outcomes in real delivery work. It mentions online/offline diagnostics, regression suites, human judging, LLM-as-a-judge where appropriate, RLVR, and feedback loops as approaches, not a requirement to implement every technique.

## Requested outputs and MVP design coverage

| Requested output | Workbench coverage |
|---|---|
| Reusable eval harnesses and rubrics for delivery agents | Shared evaluation SDK, versioned rubrics, case schema, agent adapters |
| CI/CD quality gates and cost-quality tradeoff dashboards | Hosted API and repository test bundles; gate status, quality, latency, and usage/cost comparison |
| Evaluation datasets drawn from real customer scenarios | Excel/CSV import and reviewed chat-derived cases with provenance and sanitization |
| Feedback loops that improve delivery systems over time | Answer/tool feedback becomes regression cases; compare agent revisions against a fixed dataset release |

## Evidence and scope boundaries

- Synthetic examples can demonstrate the workflow but do not satisfy evidence of real customer coverage. Obtaining permitted, sanitized customer scenarios remains an open dependency.
- Capturing feedback alone does not prove improvement. Demonstrate a detected failure, a corrected agent implementation, and a subsequent regression run.
- Judge scores are measurements under a rubric, not infallible ground truth.
- Reinforcement learning and autonomous agent rewriting are outside the MVP.
