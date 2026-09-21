export const agentPresets = {
  "synthetic-customer": {
    name: "Synthetic Customer Lookup",
    fixture_version: "synthetic-v1",
    tool_contract: "customer-lookup-v1",
    hint: "Read-only synthetic customer lookup.",
  },
  "synthetic-powerplant-var": {
    name: "VaR — Powerplant",
    fixture_version: "synthetic-powerplant-v1",
    tool_contract: "powerplant-decision-v1",
    hint: "VaR supports powerplant operators with incident analysis, risk assessments, and commercial insights.",
  },
} as const;

export type SupportedAdapter = keyof typeof agentPresets;

export function adapterContract(adapter: SupportedAdapter) {
  const { fixture_version, tool_contract } = agentPresets[adapter];
  return { adapter, fixture_version, tool_contract };
}
