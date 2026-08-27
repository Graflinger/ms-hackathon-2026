# Challenge Comparison

## Input note

The latest message listed six entries, but “Hack for Evals” appeared twice with the same description. Together with the previously supplied Token Yield challenge, there are six unique challenges to compare.

## Ranking by easiest credible hackathon build

| Rank | Challenge | Build difficulty | Evidence burden | Assessment |
|---:|---|---|---|---|
| 1 | **Transformation — No Plan, No Play** | Low | Low–medium | Narrow, explicit outputs; one input-to-score-and-draft workflow can satisfy the brief. |
| 2 | **Business Outcomes** | Low | Medium | Easy document generation, but credible ROI and measurable targets require good grounding and inputs. |
| 3 | **Evals** | Medium | Medium | Mature Azure/GitHub components exist, but a reusable harness, dataset, CI gate, dashboard, and feedback loop expand scope. |
| 4 | **2x Efficiency** | Medium | High | Building an accelerator is manageable; proving 2x requires a defensible baseline and repeated before/after measurements. |
| 5 | **Token Yield** | High | High | Requires ledger lineage, cost intervals, risk classification, FinOps mapping, and disciplined economic evidence. |
| 6 | **Scaled IP** | High | Very high | Requires repeated use, feedback signals, a visible improvement curve, and ideally reuse/revenue evidence across accounts. |

This ranks ease of meeting the brief credibly, not potential strategic value or originality.

## Recommended challenge: Transformation Qualifier

### Why it is easiest

- The requested artifact is precise: score, flag, and draft four outputs.
- It does not demand historical telemetry, production integrations, or proof of a 2x improvement.
- A deterministic rubric makes the result explainable and testable.
- Two contrasting sample engagements create a complete demo.
- It fits a small Microsoft architecture and can still look polished.

### Minimal user journey

1. A seller or delivery lead pastes discovery notes or uploads an opportunity brief.
2. AI extracts claims and evidence into structured fields, with source references.
3. The app identifies missing facts and asks targeted follow-up questions.
4. A transparent rubric scores transformation strength by dimension.
5. The app returns **Engage**, **Clarify**, or **Do not engage yet**.
6. It drafts a transformation thesis, value hypothesis, measurable outcomes, and exit criteria.
7. The user exports a reusable qualification document.

### Suggested rubric

Score each dimension from 0–4 and show evidence behind every score:

- Strategic business outcome
- Executive sponsorship and ownership
- Baseline and measurable target
- Cross-process or operating-model change
- Data/AI readiness and feasibility
- Adoption and change commitment
- Value scale and ROI hypothesis
- Exit criteria and sustainable customer capability

Add hard gates: no named business outcome, accountable owner, baseline/measurement plan, or credible transformation mechanism means the engagement cannot receive an unconditional “Engage.” Exact dimensions and thresholds remain to be decided.

### Small Microsoft-first implementation

- **UI:** Power Apps for fastest low-code delivery, or a small React/Next.js app
- **AI:** Azure AI Foundry/Azure OpenAI with structured output
- **Workflow:** Power Automate or Azure Functions
- **Storage:** Dataverse, SharePoint, or Azure Cosmos DB
- **Identity:** Microsoft Entra ID
- **Optional reporting:** Power BI

Avoid unnecessary architecture. A frontend, one structured AI call, deterministic score function, and export capability are sufficient for the MVP.

### Demo cases

- **Weak case:** a product deployment request with no baseline, owner, adoption plan, or measurable business result; it should be flagged.
- **Strong case:** an operating-model change with an executive owner, baseline, quantified target, adoption plan, and explicit exit criteria; it should qualify.
- Show that adding missing evidence changes the score predictably rather than merely asking the model again.

## Runner-up: Business Outcomes Planner

This is nearly as easy and could reuse much of the same code. It transforms discovery notes into an outcome-first account plan with targets, ROI hypotheses, and products mapped as enablers. It ranks second because generated ROI can look superficial or fabricated unless the workflow collects baseline, target, time horizon, confidence, and calculation assumptions.

## Why the original ideas fit other challenges unevenly

- The benchmark and agent-configuration checker fit **2x Efficiency** or **Evals** better than Business Outcomes or Transformation.
- The challenge leaderboard is demo-friendly, but a credible **2x** claim still needs baseline measurement.
- Image-based context compression is an optimization experiment, not by itself a match for any complete brief.
- The integrated Yield Suite matches **Token Yield** well but is considerably broader than the qualifier MVP.

## Possible strategic combination

If desired, build the Transformation Qualifier first and make its accepted output an outcome-based account-plan template. This can cover much of Business Outcomes as a stretch goal without confusing the primary submission: qualification comes first; planning follows only when the opportunity passes.
