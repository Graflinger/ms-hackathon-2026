import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type Agent,
  type AgentSpec,
  type Binding,
  type MetadataPatch,
  type Revision,
} from "../api";
import {
  useProject,
  useProjectApi,
  useDirty,
  useInstanceGuard,
  useProjectSearchParams,
} from "../project";
import {
  DateLabel,
  EmptyState,
  ErrorState,
  JsonView,
  Loading,
  Notice,
  PageHeader,
  SectionHeading,
  Status,
} from "../components";
import { MetadataForm } from "./Projects";
import {
  adapterContract,
  agentPresets,
  type SupportedAdapter,
} from "../agent-presets";

export const defaultSpec: AgentSpec = {
  schema_version: "1",
  adapter: "synthetic-customer",
  artifact: "goldenloop-demo-agent==0.2.0",
  variant: "fixed",
  modes: ["mock"],
  fixture_version: "synthetic-v1",
  tool_contract: "customer-lookup-v1",
  supports_multi_turn: true,
  trace_available: true,
  instructions: "",
  connection: null,
};

export function parseAgentSpec(text: string, bindings: Binding[]): AgentSpec {
  const spec = JSON.parse(text);
  if (!spec || typeof spec !== "object" || Array.isArray(spec))
    throw new Error("Specification must be a JSON object.");
  if (Object.keys(spec).some((key) => !Object.hasOwn(defaultSpec, key)))
    throw new Error(
      "Unknown specification field. Secret values and arbitrary configuration are not accepted.",
    );
  if (
    typeof spec.adapter !== "string" ||
    !Object.hasOwn(agentPresets, spec.adapter) ||
    spec.artifact !== defaultSpec.artifact ||
    !["fixed", "buggy"].includes(spec.variant)
  )
    throw new Error(
      "Use synthetic-customer or synthetic-powerplant-var, the pinned 0.2.0 artifact, and fixed or buggy variant.",
    );
  if (
    !Array.isArray(spec.modes) ||
    !spec.modes.length ||
    spec.modes.some((m: unknown) => m !== "mock" && m !== "live") ||
    new Set(spec.modes).size !== spec.modes.length
  )
    throw new Error("Modes must contain mock and/or live without duplicates.");
  const preset = agentPresets[spec.adapter as SupportedAdapter];
  if (
    spec.schema_version !== "1" ||
    spec.fixture_version !== preset.fixture_version ||
    spec.tool_contract !== preset.tool_contract ||
    typeof spec.instructions !== "string" ||
    typeof spec.supports_multi_turn !== "boolean" ||
    typeof spec.trace_available !== "boolean"
  )
    throw new Error(
      `Use schema version 1, ${preset.fixture_version} fixtures, ${preset.tool_contract} tools, string instructions and boolean capability declarations.`,
    );
  if (spec.connection != null) {
    const c = spec.connection;
    if (
      typeof c !== "object" ||
      Array.isArray(c) ||
      Object.keys(c).some(
        (k) =>
          ![
            "endpoint",
            "deployment",
            "api_version",
            "auth",
            "binding",
          ].includes(k),
      )
    )
      throw new Error(
        "Connection accepts metadata only, never a key, token, password or secret value.",
      );
    if (
      !bindings.some(
        (b) =>
          b.id === c.binding && b.endpoint === c.endpoint && b.auth === c.auth,
      )
    )
      throw new Error(
        "Select a server-approved binding with its exact endpoint and authentication method.",
      );
    if (
      typeof c.deployment !== "string" ||
      !c.deployment.trim() ||
      typeof c.api_version !== "string" ||
      !c.api_version.trim()
    )
      throw new Error("Connection deployment and API version are required.");
  }
  if (spec.modes.includes("live") && !spec.connection)
    throw new Error("Live mode requires a server-approved connection binding.");
  if (
    /\b(?:sk-[a-z0-9_-]{12,}|Bearer\s+\S+|(?:api[_ -]?key|password|secret|token)\s*[:=]\s*\S+)/i.test(
      text,
    )
  )
    throw new Error(
      "Remove sensitive values. Only non-secret configuration belongs in a revision.",
    );
  return spec as AgentSpec;
}

function RevisionEditor({
  agent,
  source,
  onSaved,
  onClose,
}: {
  agent: Agent;
  source?: Revision;
  onSaved: () => void;
  onClose: () => void;
}) {
  const api = useProjectApi();
  const { writeBlocked } = useProject();
  const captureInstance = useInstanceGuard();
  const client = useQueryClient();
  const [label, setLabel] = useState("");
  const [spec, setSpec] = useState<AgentSpec>(source?.spec ?? defaultSpec);
  const [advanced, setAdvanced] = useState(false);
  const [text, setText] = useState(JSON.stringify(spec, null, 2));
  const [error, setError] = useState<Error | null>(null);
  const bindings = useQuery({
    queryKey: api.key("bindings"),
    queryFn: ({ signal }) => api.bindings(signal),
  });
  const create = useMutation({
    mutationFn: ({ value }: { value: AgentSpec; isCurrent: () => boolean }) =>
      api.createRevision(agent.id, { label: label.trim(), spec: value }),
    onSuccess: (_, { isCurrent }) => {
      client.invalidateQueries({ queryKey: api.key("revisions", agent.id) });
      if (isCurrent()) {
        clearDirty();
        onSaved();
      }
    },
  });
  const clearDirty = useDirty(
    !!label ||
      advanced ||
      JSON.stringify(spec) !== JSON.stringify(source?.spec ?? defaultSpec),
  );
  function patch(value: Partial<AgentSpec>) {
    setSpec((previous) => ({ ...previous, ...value }));
  }
  const liveFieldsIncomplete = !advanced && spec.modes?.includes("live") && (
    !spec.connection?.binding ||
    !spec.connection.deployment.trim() ||
    !spec.connection.api_version.trim()
  );
  return (
    <section className="panel">
      <SectionHeading
        title={
          source
            ? `Create from ${source.label}`
            : "Create an immutable revision"
        }
        detail="Execution changes always create a new numbered revision. Existing sessions, runs and hashes remain unchanged."
      />
      <form
        className="form-stack"
        onSubmit={(e) => {
          e.preventDefault();
          if (writeBlocked || agent.archived || create.isPending) return;
          setError(null);
          try {
            create.mutate({
              value: parseAgentSpec(
                advanced ? text : JSON.stringify(spec),
                bindings.data ?? [],
              ),
              isCurrent: captureInstance(),
            });
          } catch (err) {
            setError(err as Error);
          }
        }}
      >
        <fieldset
          className="write-boundary"
          disabled={writeBlocked || agent.archived || create.isPending}
        >
          <label>
            Revision label
            <input
              required
              maxLength={200}
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="A meaningful execution version"
            />
          </label>
          {!advanced ? (
            <>
              <label>
                Adapter
                <select
                  value={spec.adapter}
                  onChange={(e) =>
                    patch(adapterContract(e.target.value as SupportedAdapter))
                  }
                >
                  {Object.entries(agentPresets).map(([id, preset]) => (
                    <option key={id} value={id}>
                      {preset.name}
                    </option>
                  ))}
                </select>
                <span className="field-hint">
                  {agentPresets[spec.adapter ?? "synthetic-customer"].hint}
                </span>
              </label>
              {spec.adapter === "synthetic-powerplant-var" && !spec.modes?.includes("live") && (
                <Notice tone="warning">
                  VaR requires a live Foundry LLM for exploration. This specification
                  currently supports mock tests only. Configure an approved Foundry
                  binding, deployment and API version to enable live execution.
                  <button
                    className="button secondary"
                    type="button"
                    onClick={() => patch({ modes: ["mock", "live"] })}
                  >
                    Configure Foundry live execution
                  </button>
                </Notice>
              )}
              <label>
                Behavior variant
                <select
                  value={spec.variant}
                  onChange={(e) =>
                    patch({ variant: e.target.value as AgentSpec["variant"] })
                  }
                >
                  <option value="fixed">Fixed</option>
                  <option value="buggy">Buggy</option>
                </select>
              </label>
              <label>
                Instructions
                <textarea
                  rows={3}
                  value={spec.instructions ?? ""}
                  onChange={(e) => patch({ instructions: e.target.value })}
                />
                <span className="field-hint">
                  Non-secret instructions. Mock execution stays deterministic;
                  instructions configure live execution.
                </span>
              </label>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={spec.modes?.includes("live") ?? false}
                  onChange={(e) =>
                    patch({
                      modes: e.target.checked ? ["mock", "live"] : ["mock"],
                      connection: e.target.checked ? spec.connection : null,
                    })
                  }
                />
                Enable live mode in addition to mock
              </label>
              {spec.modes?.includes("live") && (
                <>
                  <Notice>
                    Only operator-approved bindings for this project can be
                    selected. No credential values are accepted or displayed.
                    Registration does not make a billable connectivity probe.
                  </Notice>
                  {bindings.isPending ? (
                    <Loading label="Loading approved bindings..." />
                  ) : bindings.isError ? (
                    <ErrorState
                      error={bindings.error}
                      retry={() => bindings.refetch()}
                    />
                  ) : !bindings.data.length ? (
                    <Notice tone="warning">
                      No server-approved bindings for this project. Ask the
                      operator to configure a Foundry binding. Mock mode is
                      available for tests only; it does not call an LLM.
                    </Notice>
                  ) : null}
                  <label>
                    Connection binding
                    <select
                      required
                      value={spec.connection?.binding ?? ""}
                      onChange={(e) => {
                        const binding = bindings.data?.find(
                          (b) => b.id === e.target.value,
                        );
                        patch({
                          connection: binding
                            ? {
                                endpoint: binding.endpoint,
                                auth: binding.auth,
                                binding: binding.id,
                                deployment: "",
                                api_version: "",
                              }
                            : null,
                        });
                      }}
                    >
                      <option value="">Select approved binding</option>
                      {bindings.data?.map((b) => (
                        <option key={b.id} value={b.id}>
                          {b.id} /{" "}
                          {b.configured
                            ? "credential configured"
                            : "credential unavailable"}
                        </option>
                      ))}
                    </select>
                  </label>
                  {spec.connection && (
                    <>
                      <p className="field-hint">
                        {spec.connection.endpoint} / {spec.connection.auth}
                      </p>
                      <div className="form-row">
                        <label>
                          Deployment
                          <input
                            required
                            value={spec.connection.deployment}
                            onChange={(e) =>
                              patch({
                                connection: {
                                  ...spec.connection!,
                                  deployment: e.target.value,
                                },
                              })
                            }
                          />
                        </label>
                        <label>
                          API version
                          <input
                            required
                            value={spec.connection.api_version}
                            onChange={(e) =>
                              patch({
                                connection: {
                                  ...spec.connection!,
                                  api_version: e.target.value,
                                },
                              })
                            }
                          />
                        </label>
                      </div>
                    </>
                  )}
                  {liveFieldsIncomplete && (
                    <p className="field-hint" role="status">
                      To create a live revision, select an approved binding and
                      enter its Foundry deployment name and API version.
                    </p>
                  )}
                </>
              )}
              <p className="field-hint">
                {spec.artifact}. Defaults: {spec.fixture_version} fixtures,{" "}
                {spec.tool_contract} tools,
                multi-turn support and observable traces. Advanced JSON can
                restrict capabilities or select live-only execution.
              </p>
              <button
                className="button secondary align-start"
                type="button"
                onClick={() => {
                  setText(JSON.stringify(spec, null, 2));
                  setAdvanced(true);
                }}
              >
                Edit advanced specification JSON
              </button>
            </>
          ) : (
            <>
              <label>
                Non-secret specification JSON
                <textarea
                  className="code-editor"
                  rows={22}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  spellCheck={false}
                />
              </label>
              <p className="field-hint">
                Allowed fields: schema_version, adapter, artifact, variant,
                instructions, fixture_version, tool_contract, modes,
                supports_multi_turn, trace_available, connection. Connection
                contains endpoint, deployment, api_version, auth and an approved
                binding reference only. Never paste plaintext keys or tokens.
                {" "}VaR requires synthetic-powerplant-var, synthetic-powerplant-v1
                fixtures and powerplant-decision-v1 tools.
              </p>
              <button
                className="button secondary align-start"
                type="button"
                onClick={() => {
                  try {
                    setSpec(parseAgentSpec(text, bindings.data ?? []));
                    setAdvanced(false);
                    setError(null);
                  } catch (err) {
                    setError(err as Error);
                  }
                }}
              >
                Return to fields
              </button>
            </>
          )}
          <div className="form-actions">
            <button className="button" disabled={!label.trim() || !!liveFieldsIncomplete}>
              {create.isPending ? "Creating..." : "Create revision"}
            </button>
            <button
              className="button secondary"
              type="button"
              onClick={onClose}
            >
              Cancel
            </button>
          </div>
        </fieldset>
        {(error || create.error) && (
          <ErrorState error={error || create.error} />
        )}
      </form>
    </section>
  );
}

function AgentDetail({ agent }: { agent: Agent }) {
  const { project, writeBlocked } = useProject();
  const api = useProjectApi();
  const client = useQueryClient();
  const [editor, setEditor] = useState<Revision | "new" | null>(null);
  const [dirty, setDirty] = useState(false);
  useDirty(dirty);
  const revisions = useQuery({
    queryKey: api.key("revisions", agent.id),
    queryFn: ({ signal }) => api.revisions(agent.id, signal),
  });
  const bindings = useQuery({
    queryKey: api.key("bindings"),
    queryFn: ({ signal }) => api.bindings(signal),
  });
  const update = useMutation({
    mutationFn: (body: MetadataPatch) => api.updateAgent(agent.id, body),
    onSuccess: () => {
      setDirty(false);
      client.invalidateQueries({ queryKey: api.key("agents") });
    },
  });
  const readOnly = writeBlocked || agent.archived;
  return (
    <>
      <section className="panel">
        <SectionHeading
          title={agent.name}
          detail={`Agent ${agent.id}`}
          action={<Status value={agent.archived ? "archived" : "active"} />}
        />
        <details className="detail-body">
          <summary>Agent metadata and archive controls</summary>
          <fieldset
            className="write-boundary"
            disabled={writeBlocked}
            onChangeCapture={() => setDirty(true)}
          >
            <MetadataForm
              key={`${agent.name}-${agent.description}-${agent.archived}`}
              kind="agent"
              record={agent}
              onSave={(body) => update.mutate(body)}
              pending={update.isPending}
              error={update.error}
            />
          </fieldset>
        </details>
        <SectionHeading
          title="Revision history"
          detail="Immutable execution specifications, ordered by revision number."
          action={
            <button
              className="button"
              disabled={readOnly}
              onClick={() => setEditor("new")}
            >
              New revision
            </button>
          }
        />
        {(project.archived || agent.archived) && (
          <Notice>
            This {project.archived ? "project" : "agent"} is archived.
            Historical evidence and explicit test-bundle exports remain
            available.
          </Notice>
        )}
        {revisions.isPending ? (
          <Loading />
        ) : revisions.isError ? (
          <ErrorState
            error={revisions.error}
            retry={() => revisions.refetch()}
          />
        ) : !revisions.data.length ? (
          <EmptyState title="Define the first execution">
            Create a revision before starting a session or evaluation.
          </EmptyState>
        ) : (
          revisions.data.map((revision) => (
            <details className="check-details" key={revision.id}>
              <summary>
                <strong>
                  {revision.label} / r{revision.number}
                </strong>
                <span className="muted">
                  {revision.spec.adapter} / {revision.spec.variant} /{" "}
                  {revision.spec.modes?.join(", ")}
                </span>
              </summary>
              <div className="detail-body">
                <div className="revision-summary">
                  <code>{revision.id}</code>
                  <span>
                    Specification hash: <code>{revision.spec_hash}</code>
                  </span>
                  <DateLabel value={revision.created_at} />
                </div>
                {revision.spec_provenance === "mapping_only" && (
                  <Notice>
                    Legacy compatibility revision / mapping only. This specification
                    does not reconstruct historical execution configuration.
                  </Notice>
                )}
                <p>
                  {agentPresets[revision.spec.adapter ?? "synthetic-customer"].hint}
                </p>
                <p>
                  {revision.spec.supports_multi_turn
                    ? "Multi-turn supported"
                    : "Single-turn only"}{" "}
                  /{" "}
                  {revision.spec.trace_available
                    ? "Tool traces declared"
                    : "No traces declared"}
                </p>
                <p>
                  Connection readiness:{" "}
                  {revision.spec.connection
                    ? bindings.isError
                      ? "Unable to read binding metadata"
                      : bindings.isPending
                        ? "Loading"
                        : bindings.data?.some(
                              (b) =>
                                b.id === revision.spec.connection?.binding &&
                                b.configured,
                            )
                          ? "Binding credential configured; live opt-in and adapter dependencies are checked at execution"
                          : "Binding credential unavailable; history remains readable"
                    : "Mock execution; no connection required"}
                </p>
                <JsonView value={revision.spec} />
                <button
                  className="button secondary"
                  disabled={readOnly}
                  onClick={() => setEditor(revision)}
                >
                  Create from this revision
                </button>
              </div>
            </details>
          ))
        )}
      </section>
      {editor && (
        <RevisionEditor
          key={editor === "new" ? "new" : editor.id}
          agent={agent}
          source={editor === "new" ? undefined : editor}
          onClose={() => setEditor(null)}
          onSaved={() => setEditor(null)}
        />
      )}
    </>
  );
}

export default function Agents() {
  const { writeBlocked } = useProject();
  const api = useProjectApi();
  const client = useQueryClient();
  const [params, setParams] = useProjectSearchParams();
  const [creating, setCreating] = useState(false);
  const [dirty, setDirty] = useState(false);
  const clearDirty = useDirty(creating && dirty);
  const agents = useQuery({
    queryKey: api.key("agents"),
    queryFn: ({ signal }) => api.agents(signal),
  });
  const create = useMutation({
    mutationFn: (body: MetadataPatch) =>
      api.createAgent({ name: body.name!, description: body.description }),
    onSuccess: (agent) => {
      client.invalidateQueries({ queryKey: api.key("agents") });
      setCreating(false);
      setDirty(false);
      clearDirty();
      setParams({ agent: agent.id });
    },
  });
  const selected = agents.data?.find((a) => a.id === params.get("agent"));
  return (
    <>
      <PageHeader
        eyebrow="REGISTER THE SYSTEM"
        title="Identity before evaluation."
        description="Register logical agents, then pin their execution specifications as immutable revisions."
        action={
          <button
            className="button"
            disabled={writeBlocked}
            onClick={() => setCreating(!creating)}
          >
            New agent
          </button>
        }
      />
      <Notice>
        Register a logical agent, then choose Synthetic Customer Lookup or{" "}
        {agentPresets["synthetic-powerplant-var"].name} when creating its execution revision.
        {" "}{agentPresets["synthetic-powerplant-var"].hint}
      </Notice>
      {creating && (
        <section className="panel" onChangeCapture={() => setDirty(true)}>
          <SectionHeading title="Register an agent" />
          <fieldset className="write-boundary" disabled={writeBlocked}>
            <MetadataForm
              kind="agent"
              onSave={(body) => {
                if (!writeBlocked) create.mutate(body);
              }}
              pending={create.isPending}
              error={create.error}
            />
          </fieldset>
        </section>
      )}
      <section className="panel">
        <SectionHeading title="Project agents" />
        {agents.isPending ? (
          <Loading />
        ) : agents.isError ? (
          <ErrorState error={agents.error} retry={() => agents.refetch()} />
        ) : !agents.data.length ? (
          <EmptyState title="No agents registered">
            Create a logical agent, then its first immutable revision.
          </EmptyState>
        ) : (
          <div className="detail-body">
            <label>
              Inspect agent
              <select
                value={selected?.id ?? ""}
                onChange={(e) =>
                  setParams(e.target.value ? { agent: e.target.value } : {})
                }
              >
                <option value="">Select an agent</option>
                {agents.data.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                    {a.archived ? " / archived" : ""}
                  </option>
                ))}
              </select>
            </label>
          </div>
        )}
      </section>
      {selected && <AgentDetail key={selected.id} agent={selected} />}
    </>
  );
}
