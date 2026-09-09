import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  useProjectApi,
  useProject,
  useDirty,
  useProjectSearchParams as useSearchParams,
} from "../project";
import { AgentSelector, ExecutionIdentity } from "../agent-selector";
import { GitCompareArrows, Play, Square } from "lucide-react";
import {
  isActiveRun,
  type Revision,
  type CaseResult,
  type Mode,
  type Judge,
  type Run,
  type RunDetail,
} from "../api";
import {
  DateLabel,
  EmptyState,
  ErrorState,
  JsonView,
  Loading,
  Notice,
  PageHeader,
  SectionHeading,
  ShortId,
  Status,
  TextLink,
} from "../components";

export function comparableRuns(runs: Run[], selected: Run): Run[] {
  return runs.filter(
    (run) =>
      run.id !== selected.id &&
      run.project_id === selected.project_id &&
      run.release_id === selected.release_id,
  );
}

function ObservationTelemetry({ observation }: { observation: unknown }) {
  const data =
    observation &&
    typeof observation === "object" &&
    !Array.isArray(observation)
      ? (observation as Record<string, unknown>)
      : {};
  const latency =
    typeof data.latency_ms === "number" &&
    Number.isFinite(data.latency_ms) &&
    data.latency_ms >= 0
      ? `${data.latency_ms} ms`
      : "Not reported";
  const usage =
    data.usage && typeof data.usage === "object" && !Array.isArray(data.usage)
      ? Object.entries(data.usage)
      : [];
  return (
    <section aria-label="Case observation telemetry" className="detail-body">
      <h3>Observed agent telemetry</h3>
      <dl className="inline-definition">
        <div>
          <dt>Latency</dt>
          <dd>{latency}</dd>
        </div>
      </dl>
      <h3>Reported usage</h3>
      {usage.length ? (
        <dl className="inline-definition">
          {usage.map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>
                {value == null
                  ? "Not reported"
                  : typeof value === "object"
                    ? JSON.stringify(value)
                    : String(value)}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="muted">Not reported</p>
      )}
      <p className="field-hint">
        Per-case agent observation only. No aggregate usage, judge usage, or
        cost is inferred.
      </p>
    </section>
  );
}

function ResultEvidence({ result }: { result: CaseResult }) {
  return (
    <details className="result-case">
      <summary>
        <div>
          <strong>Case {result.case_id}</strong>
          <span className="cell-sub">
            Revision {result.case_revision} / {result.checks.length} check
            results
          </span>
        </div>
        <Status value={result.gate} />
      </summary>
      <div className="result-checks">
        <ObservationTelemetry observation={result.observation} />
        {!result.checks.length && (
          <Notice tone="warning">
            No check results reported. Missing evaluation evidence is not a
            pass.
          </Notice>
        )}
        {result.checks.map((check, index) => (
          <details className="check-details" key={`${check.id}-${index}`}>
            <summary>
              <div>
                <strong>{check.kind}</strong>
                <span className="cell-sub">
                  {check.id} / {check.reason || "No reason reported"}
                </span>
              </div>
              <Status value={check.status} />
            </summary>
            <div className="detail-body">
              <dl className="inline-definition">
                <div>
                  <dt>Score</dt>
                  <dd>{check.score ?? "Not reported"}</dd>
                </div>
                <div>
                  <dt>Evaluator version</dt>
                  <dd>{check.evaluator_version || "Not reported"}</dd>
                </div>
              </dl>
              <span className="small-label">ACTUAL EVIDENCE</span>
              <JsonView value={check.evidence} />
            </div>
          </details>
        ))}
        <details className="help-details">
          <summary>Agent observation</summary>
          <JsonView value={result.observation} />
        </details>
      </div>
    </details>
  );
}

function RunSignals({ run }: { run: RunDetail }) {
  return (
    <>
      <div className="run-signals">
        <div>
          <span className="small-label">EXECUTION</span>
          <Status value={run.status} />
        </div>
        <div>
          <span className="small-label">QUALITY GATE</span>
          <Status value={run.gate} />
        </div>
        <div>
          <span className="small-label">AGENT / MODE</span>
          <strong>
            {run.agent_name} / {run.agent_revision_label} / {run.mode}
          </strong>
        </div>
        <div>
          <span className="small-label">LATENCY / USAGE</span>
          <span className="muted">Not reported at run level</span>
        </div>
      </div>
      <ExecutionIdentity value={run} />
      {run.error != null && (
        <div className="run-error">
          <h3>Execution / evaluation error</h3>
          <JsonView value={run.error} />
        </div>
      )}
      {[
        "incomplete",
        "error",
        "failed",
        "interrupted",
        "cancelled",
        "canceled",
      ].includes(run.status) && (
        <Notice tone="warning">
          This execution is {run.status}. Do not interpret incomplete or failed
          execution as a completed quality-gate result.
        </Notice>
      )}
    </>
  );
}

function Comparison({
  first,
  secondId,
}: {
  first: RunDetail;
  secondId: string;
}) {
  const api = useProjectApi();
  const second = useQuery({
    queryKey: api.key("run", secondId),
    queryFn: ({ signal }) => api.run(secondId, signal),
    refetchInterval: (query) =>
      query.state.data && isActiveRun(query.state.data) ? 2000 : false,
  });
  if (second.isPending) return <Loading label="Loading comparison run..." />;
  if (second.isError)
    return <ErrorState error={second.error} retry={() => second.refetch()} />;
  if (
    first.project_id !== second.data.project_id ||
    first.release_id !== second.data.release_id
  )
    return (
      <Notice tone="warning">
        Comparison blocked: both runs must use the same immutable release.
      </Notice>
    );
  const other = second.data;
  const settings = (run: RunDetail) => {
    const lineage = run.lineage ?? {};
    return {
      mode: run.mode,
      fixture: lineage.fixture_version ?? "Not reported",
      sdk: lineage.sdk_version ?? "Not reported",
      agent_spec:
        lineage.agent_spec ?? "Unavailable for this historical execution",
      judge: lineage.judge ?? "Not reported",
      provider_version:
        lineage.provider_version ??
        lineage.model_version ??
        "Not reported; deployment names do not freeze provider versions",
    };
  };
  const keys = [
    ...new Set(
      [...(first.results ?? []), ...(other.results ?? [])].map(
        (result) => `${result.case_id}:${result.case_revision}`,
      ),
    ),
  ];
  return (
    <section className="comparison">
      <SectionHeading
        title="Same dataset. Different behavior."
        detail="Compare gate outcomes and inspect evidence. A missing result is not a pass."
      />
      <div className="comparison-head">
        {[first, other].map((run) => (
          <div key={run.id}>
            <span className="small-label">
              {run.id === first.id ? "SELECTED RUN" : "COMPARISON RUN"}
            </span>
            <h3>
              {run.agent_name} / {run.agent_revision_label}{" "}
              <span className="muted">/ {run.mode}</span>
            </h3>
            <ExecutionIdentity value={run} />
            <ShortId value={run.id} />
            <div className="inline-meta">
              <Status value={run.status} />
              <Status value={run.gate} />
            </div>
          </div>
        ))}
      </div>
      <details className="help-details" open>
        <summary>Execution and evaluator configuration</summary>
        <div className="comparison-head">
          <div>
            <h3>Selected run configuration</h3>
            <JsonView value={settings(first)} />
          </div>
          <div>
            <h3>Comparison run configuration</h3>
            <JsonView value={settings(other)} />
          </div>
        </div>
      </details>
      {first.mode !== other.mode && (
        <Notice tone="warning">
          These runs use different modes. Differences cannot be attributed
          solely to the agent revision.
        </Notice>
      )}
      {JSON.stringify(settings(first)) !== JSON.stringify(settings(other)) && (
        <Notice tone="warning">
          Execution or evaluator configuration differs. Compare fixture,
          artifact, instructions, provider/model evidence and judge snapshots
          below; do not attribute all changes solely to agent behavior. Missing
          provider version evidence remains unknown.
        </Notice>
      )}
      {first.agent_revision === other.agent_revision && (
        <Notice>
          Both runs use the same agent revision. Inspect execution and evaluator
          settings before attributing differences.
        </Notice>
      )}
      {!keys.length ? (
        <EmptyState title="No case results to compare">
          Results appear when the backend reports them. Active runs are polled
          automatically.
        </EmptyState>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Case / revision</th>
                <th>{first.agent_revision} / selected</th>
                <th>{other.agent_revision} / comparison</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((key) => {
                const left = first.results?.find(
                  (result) =>
                    `${result.case_id}:${result.case_revision}` === key,
                );
                const right = other.results?.find(
                  (result) =>
                    `${result.case_id}:${result.case_revision}` === key,
                );
                return (
                  <tr key={key}>
                    <td>
                      <ShortId value={(left || right)!.case_id} />
                      <div className="cell-sub">
                        r{(left || right)!.case_revision}
                      </div>
                    </td>
                    {[left, right].map((result, index) => (
                      <td key={index}>
                        {result ? (
                          <>
                            <Status value={result.gate} />
                            <details className="comparison-evidence">
                              <summary>Check outcomes & evidence</summary>
                              <ObservationTelemetry
                                observation={result.observation}
                              />
                              {result.checks.length ? (
                                result.checks.map((check, checkIndex) => (
                                  <div
                                    className="compare-check"
                                    key={check.id || checkIndex}
                                  >
                                    <strong>{check.kind}</strong>
                                    <Status value={check.status} />
                                    <p>{check.reason}</p>
                                    <JsonView
                                      value={{
                                        id: check.id,
                                        score: check.score,
                                        evaluator_version:
                                          check.evaluator_version,
                                        evidence: check.evidence,
                                      }}
                                    />
                                  </div>
                                ))
                              ) : (
                                <p>No checks reported.</p>
                              )}
                            </details>
                          </>
                        ) : (
                          <Status value="incomplete" />
                        )}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <details className="help-details">
        <summary>Comparison lineage and errors</summary>
        <JsonView
          value={{
            selected: {
              id: first.id,
              lineage: first.lineage,
              error: first.error,
            },
            comparison: {
              id: other.id,
              lineage: other.lineage,
              error: other.error,
            },
          }}
        />
      </details>
    </section>
  );
}

function RunInspector({ runId, allRuns }: { runId: string; allRuns: Run[] }) {
  const api = useProjectApi();
  const client = useQueryClient();
  const [compareId, setCompareId] = useState("");
  const [cancelConfirmed, setCancelConfirmed] = useState(false);
  const detail = useQuery({
    queryKey: api.key("run", runId),
    queryFn: ({ signal }) => api.run(runId, signal),
    refetchInterval: (query) =>
      query.state.data && isActiveRun(query.state.data) ? 2000 : false,
  });
  const cancel = useMutation({
    mutationFn: () => api.cancelRun(runId),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: api.key("run", runId) });
      client.invalidateQueries({ queryKey: api.key("runs") });
      setCancelConfirmed(false);
    },
  });
  if (detail.isPending)
    return <Loading label="Loading evaluation evidence..." />;
  if (detail.isError)
    return <ErrorState error={detail.error} retry={() => detail.refetch()} />;
  const run = detail.data;
  const options = comparableRuns(allRuns, run);
  return (
    <section className="panel">
      <SectionHeading
        title={run.release_name || `Release ${run.release_id}`}
        detail={`Run ${run.id}`}
        action={
          isActiveRun(run) && (
            <button
              className="button small danger"
              onClick={() => setCancelConfirmed((value) => !value)}
            >
              <Square size={13} />
              Cancel run
            </button>
          )
        }
      />
      <div className="detail-body">
        <RunSignals run={run} />
        {isActiveRun(run) && (
          <Notice>
            Evaluation in progress. Status and results refresh automatically. A
            missing gate is not a pass.
          </Notice>
        )}
        {cancelConfirmed && (
          <div className="approval-box">
            <p>
              Request cancellation of this run? Partial results may remain
              incomplete.
            </p>
            <button
              className="button danger"
              disabled={cancel.isPending}
              onClick={() => cancel.mutate()}
            >
              {cancel.isPending ? "Requesting..." : "Confirm cancellation"}
            </button>
          </div>
        )}
        {cancel.isSuccess && (
          <Notice>
            Cancellation requested. The reported backend status is shown above.
          </Notice>
        )}
        {cancel.error && <ErrorState error={cancel.error} />}
        <details className="help-details">
          <summary>Version lineage & run metadata</summary>
          <JsonView
            value={{
              id: run.id,
              release_id: run.release_id,
              agent_revision: run.agent_revision,
              mode: run.mode,
              created_at: run.created_at,
              lineage: run.lineage ?? "Not reported",
            }}
          />
        </details>
        <div className="comparison-picker">
          <GitCompareArrows size={20} />
          <label>
            Compare with another run of this release
            <select
              value={compareId}
              onChange={(event) => setCompareId(event.target.value)}
            >
              <option value="">Select a comparison run</option>
              {options.map((other) => (
                <option key={other.id} value={other.id}>
                  {other.agent_name} / {other.agent_revision_label} /{" "}
                  {other.mode} / {other.status} / {other.id.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
        </div>
        {!options.length && (
          <p className="field-hint">
            Run another registered agent revision against this same release to
            compare behavior.
          </p>
        )}
        {compareId ? (
          <Comparison first={run} secondId={compareId} />
        ) : (
          <>
            <h3 className="subsection-title">Case results</h3>
            {!run.results?.length ? (
              <EmptyState
                title={
                  isActiveRun(run)
                    ? "Waiting for case results"
                    : "No case results reported"
                }
              >
                Only backend-reported outcomes are shown. Missing traces and
                unconfigured judges must not silently count as passes.
              </EmptyState>
            ) : (
              run.results.map((result, index) => (
                <ResultEvidence
                  key={`${result.case_id}-${result.case_revision}-${index}`}
                  result={result}
                />
              ))
            )}
          </>
        )}
      </div>
    </section>
  );
}

export default function Runs() {
  const api = useProjectApi();
  const { project, writeBlocked } = useProject();
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const selected = params.get("run");
  const [releaseId, setReleaseId] = useState(params.get("release") || "");
  const [revision, setRevision] = useState<Revision | null>(null);
  const [mode, setMode] = useState<Mode>("mock");
  const [liveConfirmed, setLiveConfirmed] = useState(false);
  const [judge, setJudge] = useState<Judge>("none");
  const [judgeConfirmed, setJudgeConfirmed] = useState(false);
  const [showLaunch, setShowLaunch] = useState(!!params.get("release"));
  const clearDirty = useDirty(
    showLaunch && (!!revision || liveConfirmed || judgeConfirmed),
  );
  // Keep the key for an identical retry after an ambiguous network failure.
  const submission = useRef<{ signature: string; key: string } | null>(null);
  const runs = useQuery({
    queryKey: api.key("runs"),
    queryFn: ({ signal }) => api.runs(signal),
    refetchInterval: (query) =>
      query.state.data?.some(isActiveRun) ? 2500 : false,
  });
  const releases = useQuery({
    queryKey: api.key("releases"),
    queryFn: ({ signal }) => api.releases(signal),
  });
  const launch = useMutation({
    mutationFn: () => {
      if (writeBlocked || !revision || !revision.spec.modes?.includes(mode))
        throw new Error("Select an active compatible agent revision and mode.");
      if (
        (mode === "live" && !liveConfirmed) ||
        (judge === "azure" && !judgeConfirmed)
      ) {
        throw new Error(
          "Confirm each selected live provider's usage costs before starting.",
        );
      }
      const signature = JSON.stringify({
        projectId: project.id,
        releaseId,
        revision: revision.id,
        mode,
        judge,
      });
      if (submission.current?.signature !== signature)
        submission.current = { signature, key: crypto.randomUUID() };
      return api.startRun(
        releaseId,
        revision.id,
        mode,
        submission.current.key,
        judge,
      );
    },
    onSuccess: (run) => {
      submission.current = null;
      client.invalidateQueries({ queryKey: api.key("runs") });
      client.invalidateQueries({ queryKey: api.key("summary") });
      clearDirty();
      setParams({ run: run.id });
      setShowLaunch(false);
    },
  });
  return (
    <>
      <PageHeader
        eyebrow="05 / CLOSE THE LOOP"
        title="Let the evidence speak."
        description="Evaluate a frozen release against an agent revision. Keep execution health separate from quality, then compare what actually changed."
        action={
          <button
            className="button"
            disabled={writeBlocked}
            onClick={() => setShowLaunch((value) => !value)}
          >
            <Play size={16} />
            New evaluation
          </button>
        }
      />
      <div className="run-principle">
        <span className="small-label">TWO SIGNALS. ONE HONEST PICTURE.</span>
        <p>
          <strong>Execution</strong> tells you whether evaluation completed.{" "}
          <strong>Gate</strong> tells you whether the reported checks met the
          standard.
        </p>
      </div>
      {showLaunch && (
        <section className="panel">
          <SectionHeading
            title="Configure an evaluation"
            detail="Run registered agent revisions separately against the same release to compare their behavior."
          />
          <form
            className="form-stack"
            onSubmit={(event) => {
              event.preventDefault();
              if (
                writeBlocked ||
                !revision ||
                !revision.spec.modes?.includes(mode) ||
                launch.isPending ||
                !releases.data?.some((release) => release.id === releaseId) ||
                (mode === "live" && !liveConfirmed) ||
                (judge === "azure" && !judgeConfirmed)
              )
                return;
              launch.mutate();
            }}
          >
            {releases.isPending ? (
              <Loading label="Loading releases..." />
            ) : releases.isError ? (
              <ErrorState
                error={releases.error}
                retry={() => releases.refetch()}
              />
            ) : !releases.data.length ? (
              <EmptyState
                title="A release is required"
                action={
                  <TextLink to="/releases">Create a golden release</TextLink>
                }
              >
                Publish approved cases before launching an evaluation.
              </EmptyState>
            ) : (
              <label>
                Golden release
                <select
                  required
                  value={releaseId}
                  disabled={launch.isPending}
                  onChange={(event) => setReleaseId(event.target.value)}
                >
                  <option value="">Select an immutable release</option>
                  {releases.data.map((release) => (
                    <option key={release.id} value={release.id}>
                      {release.name} / {release.case_count} cases /{" "}
                      {release.id.slice(0, 8)}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <div className="form-row">
              <AgentSelector
                value={revision}
                disabled={launch.isPending || writeBlocked}
                onChange={(value) => {
                  setRevision(value);
                  setMode(value?.spec.modes?.[0] ?? "mock");
                  setLiveConfirmed(false);
                  setJudgeConfirmed(false);
                }}
              />
              <label>
                Execution mode
                <select
                  value={mode}
                  disabled={launch.isPending}
                  onChange={(event) => {
                    setMode(event.target.value as Mode);
                    setLiveConfirmed(false);
                  }}
                >
                  <option
                    value="mock"
                    disabled={!revision?.spec.modes?.includes("mock")}
                  >
                    Mock / synthetic deterministic execution
                  </option>
                  <option
                    value="live"
                    disabled={!revision?.spec.modes?.includes("live")}
                  >
                    Live / requires backend configuration
                  </option>
                </select>
              </label>
            </div>
            {mode === "live" ? (
              <>
                <Notice tone="warning">
                  Live execution requires configured model deployments and
                  credentials on the backend. This UI does not provision or
                  verify cloud integration. Judge checks require a configured
                  judge; configuration errors must remain visible.
                </Notice>
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    required
                    checked={liveConfirmed}
                    disabled={launch.isPending}
                    onChange={(event) => setLiveConfirmed(event.target.checked)}
                  />
                  I intend to invoke the configured live agent, which may incur
                  model usage costs.
                </label>
              </>
            ) : (
              <Notice>
                Mock agent execution is synthetic, not a cloud-agent evaluation.
                An independently selected Azure judge still calls a live model
                and may incur usage costs. No scores are invented here.
              </Notice>
            )}
            <label>
              Judge provider
              <select
                value={judge}
                disabled={launch.isPending}
                onChange={(event) => {
                  setJudge(event.target.value as Judge);
                  setJudgeConfirmed(false);
                }}
              >
                <option value="none">None / no judge provider</option>
                <option value="azure">
                  Azure / live judge, requires server configuration
                </option>
              </select>
            </label>
            {judge === "azure" ? (
              <>
                <Notice tone="warning">
                  Azure judging is independent of agent execution mode,
                  including mock mode. The server needs{" "}
                  <code>GOLDENLOOP_ALLOW_LIVE_SYNTHETIC_JUDGE=true</code>,{" "}
                  <code>GOLDENLOOP_JUDGE_ENDPOINT</code>,{" "}
                  <code>GOLDENLOOP_JUDGE_DEPLOYMENT</code>,{" "}
                  <code>GOLDENLOOP_JUDGE_API_VERSION</code>, and{" "}
                  <code>GOLDENLOOP_JUDGE_API_KEY</code>. This UI does not
                  configure, accept, or verify secrets. Missing configuration is
                  reported by the backend before queuing.
                </Notice>
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    required
                    disabled={launch.isPending}
                    checked={judgeConfirmed}
                    onChange={(event) =>
                      setJudgeConfirmed(event.target.checked)
                    }
                  />
                  I authorize Azure judge model usage costs, independently of
                  agent execution costs.
                </label>
              </>
            ) : (
              <Notice>
                No judge provider is selected. Releases with required judge
                checks are rejected with HTTP 409 before queuing. Selecting a
                live agent does not enable a judge.
              </Notice>
            )}
            {launch.error && <ErrorState error={launch.error} />}
            <div className="form-actions">
              <button
                className="button"
                disabled={
                  writeBlocked ||
                  !revision ||
                  !revision.spec.modes?.includes(mode) ||
                  launch.isPending ||
                  !releaseId ||
                  !releases.data?.some((release) => release.id === releaseId) ||
                  (mode === "live" && !liveConfirmed) ||
                  (judge === "azure" && !judgeConfirmed)
                }
              >
                <Play size={15} />
                {launch.isPending ? "Starting..." : "Start evaluation"}
              </button>
              <button
                className="button secondary"
                type="button"
                onClick={() => setShowLaunch(false)}
              >
                Close
              </button>
            </div>
          </form>
        </section>
      )}
      <section className="panel">
        <SectionHeading
          title="Run ledger"
          detail="The complete evaluation history returned by the API."
        />
        {runs.isPending ? (
          <Loading />
        ) : runs.isError ? (
          <ErrorState error={runs.error} retry={() => runs.refetch()} />
        ) : !runs.data.length ? (
          <EmptyState
            title="No evaluations yet"
            action={
              <button
                className="button secondary"
                disabled={writeBlocked}
                onClick={() => setShowLaunch(true)}
              >
                Configure the first run
              </button>
            }
          >
            Choose a golden release and an agent revision. Results and evidence
            appear here after execution starts.
          </EmptyState>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Run / release</th>
                  <th>Agent</th>
                  <th>Mode</th>
                  <th>Execution</th>
                  <th>Gate</th>
                  <th>Created</th>
                  <th>
                    <span className="sr-only">Action</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {[...runs.data]
                  .sort((a, b) => b.created_at.localeCompare(a.created_at))
                  .map((run) => (
                    <tr
                      key={run.id}
                      className={selected === run.id ? "selected-row" : ""}
                    >
                      <td>
                        <strong>{run.release_name || run.release_id}</strong>
                        <div className="cell-sub">
                          <ShortId value={run.id} />
                        </div>
                      </td>
                      <td>
                        {run.agent_name} / {run.agent_revision_label}
                      </td>
                      <td>{run.mode}</td>
                      <td>
                        <Status value={run.status} />
                      </td>
                      <td>
                        <Status value={run.gate} />
                      </td>
                      <td>
                        <DateLabel value={run.created_at} />
                      </td>
                      <td>
                        <button
                          className="button small secondary"
                          onClick={() => setParams({ run: run.id })}
                        >
                          Inspect<span className="sr-only"> run {run.id}</span>
                        </button>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {selected && (
        <RunInspector
          key={selected}
          runId={selected}
          allRuns={runs.data || []}
        />
      )}
    </>
  );
}
