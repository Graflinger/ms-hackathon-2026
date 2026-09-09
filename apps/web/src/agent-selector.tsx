import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { type Revision, type Run, type Session } from "./api";
import { useProjectApi } from "./project";
import { ErrorState, Loading, Notice, TextLink } from "./components";

export function AgentSelector({
  value,
  onChange,
  disabled,
  purpose = "run",
}: {
  value: Revision | null;
  onChange: (revision: Revision | null) => void;
  disabled?: boolean;
  purpose?: "run" | "chat" | "export";
}) {
  const api = useProjectApi();
  const [agentId, setAgentId] = useState(value?.agent_id ?? "");
  const agents = useQuery({
    queryKey: api.key("agents"),
    queryFn: ({ signal }) => api.agents(signal),
  });
  const revisions = useQuery({
    queryKey: api.key("revisions", agentId),
    queryFn: ({ signal }) => api.revisions(agentId, signal),
    enabled: !!agentId,
  });
  const available =
    agents.data?.filter((a) => purpose === "export" || !a.archived) ?? [];
  useEffect(() => {
    if (!value) return;
    const unavailable =
      agents.data &&
      !agents.data.some(
        (a) => a.id === value.agent_id && (purpose === "export" || !a.archived),
      );
    if (agents.isError || unavailable) onChange(null);
  }, [agents.data, agents.isError, purpose, value, onChange]);
  const compatible = (revision: Revision) =>
    purpose !== "chat" ||
    (revision.spec.modes?.includes("mock") &&
      revision.spec.supports_multi_turn &&
      revision.spec.trace_available);
  return (
    <div className="agent-selector">
      {agents.isPending ? (
        <Loading label="Loading registered agents..." />
      ) : agents.isError ? (
        <ErrorState error={agents.error} retry={() => agents.refetch()} />
      ) : !available.length ? (
        <Notice>
          No {purpose === "export" ? "registered" : "active"} agents.{" "}
          <TextLink to="/agents">Manage agents and revisions</TextLink>
        </Notice>
      ) : null}
      <div className="form-row">
        <label>
          Agent
          <select
            required
            value={agentId}
            disabled={disabled}
            onChange={(e) => {
              setAgentId(e.target.value);
              onChange(null);
            }}
          >
            <option value="">Select a registered agent</option>
            {available.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
                {a.archived ? " / archived" : ""}
              </option>
            ))}
          </select>
        </label>
        <label>
          Agent revision
          <select
            required
            value={value?.id ?? ""}
            disabled={
              disabled || !agentId || !available.some((a) => a.id === agentId)
            }
            onChange={(e) =>
              onChange(
                revisions.data?.find((r) => r.id === e.target.value) ?? null,
              )
            }
          >
            <option value="">Select an immutable revision</option>
            {revisions.data?.map((r) => (
              <option key={r.id} value={r.id} disabled={!compatible(r)}>
                {r.label} / r{r.number}
                {!compatible(r) ? " / not Playground compatible" : ""}
              </option>
            ))}
          </select>
        </label>
      </div>
      {agentId && revisions.isPending && (
        <Loading label="Loading revision history..." />
      )}
      {revisions.isError && (
        <ErrorState error={revisions.error} retry={() => revisions.refetch()} />
      )}
      {agentId && revisions.data?.length === 0 && (
        <Notice>
          This agent has no revisions yet.{" "}
          <TextLink to={`/agents?agent=${encodeURIComponent(agentId)}`}>
            Create a revision
          </TextLink>
        </Notice>
      )}
      {value && (
        <div className="revision-summary">
          <strong>
            {value.label} / revision {value.number}
          </strong>
          <code>{value.id}</code>
          <code title="Specification hash">{value.spec_hash}</code>
          <span>
            {value.spec.adapter} / {value.spec.variant} /{" "}
            {value.spec.modes?.join(", ")} / {value.spec.fixture_version}
          </span>
          <span>
            {value.spec.supports_multi_turn ? "Multi-turn" : "Single-turn"} /{" "}
            {value.spec.trace_available
              ? "Tool traces declared"
              : "No tool traces"}{" "}
            / {value.spec.tool_contract}
          </span>
          {value.spec_provenance === "mapping_only" && (
            <span>
              Legacy compatibility revision / mapping only. This specification
              does not reconstruct historical execution configuration.
            </span>
          )}
        </div>
      )}
    </div>
  );
}

export function ExecutionIdentity({ value }: { value: Run | Session }) {
  return (
    <div className="revision-summary">
      <strong>
        {value.agent_name} / {value.agent_revision_label}
      </strong>
      <code>{value.agent_revision_id}</code>
      <span>
        Specification hash:{" "}
        <code>
          {value.spec_hash ?? "Unavailable for this historical execution"}
        </code>
      </span>
      {value.legacy && (
        <span>
          Legacy compatibility record; original evidence retains{" "}
          {value.agent_revision}.
        </span>
      )}
    </div>
  );
}
