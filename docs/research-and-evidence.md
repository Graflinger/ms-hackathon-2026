# Research and Evidence Register

The challenge explicitly requires disciplined claims. Add every material product claim here before using it in a pitch.

## Evidence labels

- **Observed:** measured in our prototype or dataset; describe conditions and sample size.
- **Specified:** algorithm, interface, or process is fully described but not validated.
- **Proposed:** idea or intended method not yet fully implemented.
- **Externally supported:** backed by a cited external source, but not necessarily reproduced by us.
- **Validated:** meets a predefined validation plan on representative data. Use sparingly.

## Claim register

| Claim | Current status | Needed evidence / limitation |
|---|---|---|
| Image representations make token use more efficient than text. | Proposed; unverified and too broad | Test specific models, image settings, prices, tasks, acceptance quality, retries, latency, and accessibility. Compare total outcome cost, not token count alone. |
| Shorter agent instructions reduce cost without reducing quality. | Proposed | Controlled benchmark across representative tasks; account for retries and acceptance. |
| Fewer or more targeted MCP tools improve agent yield. | Proposed | Define “stale” and “too many”; measure selection accuracy, context overhead, task success, and cost. |
| Cost distributions have commercially important expensive tails. | Challenge premise; not yet observed in this project | Demonstrate with real or clearly labeled synthetic run data; report distribution and assumptions. |
| The estimation model predicts presales cost intervals reliably. | Not yet specified or validated | Define calibration/backtesting protocol; until then call intervals scenario-based estimates. |

## Research backlog

1. Confirm telemetry and billing granularity available from Azure AI Foundry/Azure OpenAI, GitHub Models, and GitHub Copilot.
2. Review Microsoft FinOps guidance and Cost Management allocation/export schemas.
3. Select defensible interval and tail-risk methods suitable for sparse presales data.
4. Define a practical engagement risk rubric.
5. Design acceptance-event and human-rework capture.
6. Test image-versus-text economics under controlled conditions if retained.

## Minimum experiment record

For each experiment retain: hypothesis, task and dataset, model/version, configuration, date, price assumptions, number of attempts, acceptance rule, token/compute cost, latency, retries, human effort, result, and limitations.
