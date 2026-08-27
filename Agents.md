# InSpireD Hackathon — Project Guide

This is the durable entry point for humans and AI agents working in this repository. Keep it concise and update it whenever a discussion changes the project's goals, scope, assumptions, decisions, or open questions. Put detailed material in the linked documents rather than expanding this file indefinitely.

## Mission

Build a focused, Microsoft-based hackathon prototype that directly satisfies one InSpireD executive challenge and can be demonstrated credibly end to end.

## Challenge selection

Six challenge descriptions have been supplied, although **Evals was duplicated**, leaving five new unique challenges plus the original Token Yield challenge. The current recommendation for the easiest credible build is **Hack for Transformation — No Plan, No Play**.

See [challenge comparison](docs/challenge-comparison.md) for the ranking and scope of each challenge. The original [Token Yield brief](docs/challenge-brief.md) remains available.

## Current direction

Build a narrow **Transformation Qualifier**: a user pastes an opportunity/discovery summary, the system asks for missing evidence, scores it against a transparent rubric, flags weak or non-transformational work, and drafts a transformation thesis, value hypothesis, measurable outcomes, and exit criteria.

This is a recommendation, not yet an accepted decision. The original Token Yield concepts remain documented in [ideas and assessment](docs/ideas.md).

## Microsoft-first implementation bias

Prefer Microsoft technologies where they fit: Azure AI Foundry/Azure OpenAI, Microsoft Cost Management, Azure Monitor/Application Insights, OpenTelemetry, Power BI or Microsoft Fabric, GitHub Copilot/GitHub Models, Azure Functions or Container Apps, and Microsoft Entra ID. Do not add services merely to maximize the Microsoft technology count.

## Working principles

- Match the selected challenge's requested outputs explicitly.
- Keep scoring deterministic and explainable; use AI to extract evidence and draft content.
- Do not let the LLM invent missing customer facts, baselines, or ROI numbers.
- Distinguish supplied facts, model inferences, assumptions, and missing evidence.
- Separate measured facts from assumptions and proposals.
- Never describe a prototype or synthetic result as production-validated.
- Keep the hackathon scope demoable end to end.
- Treat potentially sensitive prompts, outputs, identities, and billing data accordingly.

## Documentation map

- [Challenge brief](docs/challenge-brief.md) — problem, required outputs, and constraints
- [Challenge comparison](docs/challenge-comparison.md) — unique challenges, difficulty ranking, and recommendation
- [Ideas and assessment](docs/ideas.md) — candidate concepts, fit, risks, and recommendation
- [Decisions](docs/decisions.md) — accepted decisions and pending choices
- [Research and evidence](docs/research-and-evidence.md) — claims, sources to find, experiments, and evidence status
- [Session log](docs/session-log.md) — concise chronological record

## Current open questions

1. What does the “caveman” idea mean?
2. Do we accept the Transformation Qualifier recommendation or choose another challenge?
3. Which transformation dimensions and thresholds should form the qualification rubric?
4. Can sanitized real opportunity/discovery examples be used, or should the demo use synthetic cases?
5. What input form is most useful: pasted notes, uploaded document, structured questionnaire, or a combination?

## Repository state

- Git repository: initialized (no initial commit at the time this guide was created)
- Project phase: challenge selection
- Last updated: 2026-08-25
