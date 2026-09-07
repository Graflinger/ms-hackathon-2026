# Product Scope — GoldenLoop

Status: GoldenLoop is the selected project name; user goals and accepted CI paths are captured. Other MVP cuts and implementation details below remain recommendations pending confirmation.

## Product thesis

Turn spreadsheet examples and interactive debugging into reviewable, versioned regression tests that run both in the workbench and in an agent's own repository. The differentiator is the feedback-to-test loop, including tool behavior, rather than merely another judge dashboard.

## Primary workflows

### 1. Import initial scenarios

Upload `.xlsx` or CSV, map columns, preview parsed rows and errors, then import candidates. Support simple question/reference-answer pairs first and scenario grouping with ordered turns. Track file/sheet/row provenance. Do not require nontechnical users to author nested JSON in spreadsheets.

### 2. Chat and inspect

Select the configured target agent and conduct a multi-turn conversation. Display answers beside a trace timeline of tool names, arguments, results, timing, and errors. Annotate either an answer or a specific tool call, including corrected arguments, missing/forbidden calls, or expected behavior.

This requires an instrumented agent or an explicit trace-return contract. An answer-only endpoint cannot reveal internal tool calls. Execution traces are not hidden chain-of-thought. If traces are unavailable, label the limitation rather than infer calls from text.

### 3. Curate the golden dataset

Save an interaction and its feedback as a candidate case, including required conversation context. Review expectations, edit checks, and approve selected revisions into a golden release. Keep actual bad behavior separate from desired behavior. A thumbs-down with no correction is useful triage but insufficient reference truth.

### 4. Evaluate and diagnose

Choose a dataset release, agent revision, evaluator configuration, and execution mode. Run answer and tool checks plus a judge rubric; inspect evidence and errors per case. Compare runs for quality, latency, and usage/cost when available. Show unknown cost as unknown, and distinguish agent execution from judge cost.

### 5. Reuse from CI/CD

- Hosted: request agent invocation and evaluation; consume a machine-readable gate result. Hosted scoring of externally generated answers/traces is deferred.
- Repository-local: export a pinned case bundle and a thin test wrapper using the same SDK. Run without depending on this workbench's backend.

## Recommended MVP boundary

- One workbench UI and backend; one configured, instrumented agent with safe demo tools.
- Excel and CSV import with mapping and row validation.
- Single-turn cases and deterministic scripted multi-turn scenarios.
- Answer feedback and tool argument/behavior annotations.
- Candidate editing, explicit approval, and immutable golden releases.
- A small evaluator set: required/forbidden content, structured output, tool name/argument assertions, and one judge rubric.
- One SDK language, hosted execution API, and exported tests using the same core.
- Results view and one CI example showing a failed gate, a code fix, and a passing gate.

Defer autonomous user simulation, multiple agent-framework adapters, two-language SDKs, multi-tenant administration, model training, and a full observability platform. Production hardening is separate; access control, redaction, and safe tool execution remain required for any real data.

## Demo / acceptance story

1. Import a small spreadsheet and correct one validation error.
2. Chat with the demo agent; observe a tool call using the wrong customer identifier despite a plausible final answer.
3. Annotate the parameter, verify the correction, and publish a golden case.
4. Run evaluation: the tool assertion fails even if the answer judge is satisfied.
5. Fix the demo agent and rerun against the same release.
6. Trigger the hosted check from CI, then export and run equivalent checks in a small agent-repository example.
7. Edit a case into a new release; show that prior results retain their original expectations.

Synthetic identifiers and fixtures must be labeled. Customer coverage requires separately approved real scenarios.
