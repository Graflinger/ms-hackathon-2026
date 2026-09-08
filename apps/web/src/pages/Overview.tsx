import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Upload } from "lucide-react";
import { Link } from "react-router-dom";
import { api, isActiveRun } from "../api";
import {
  DateLabel,
  EmptyState,
  ErrorState,
  Loading,
  PageHeader,
  SectionHeading,
  ShortId,
  Status,
  TextLink,
} from "../components";

export default function Overview() {
  const summary = useQuery({
    queryKey: ["summary"],
    queryFn: ({ signal }) => api.summary(signal),
  });
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: ({ signal }) => api.runs(signal),
    refetchInterval: (query) =>
      query.state.data?.some(isActiveRun) ? 2500 : false,
  });
  const cases = useQuery({
    queryKey: ["cases"],
    queryFn: ({ signal }) => api.cases(signal),
  });
  return (
    <>
      <PageHeader
        eyebrow="FROM DEMOS TO LEARNING SYSTEMS"
        title="Make every answer count."
        description="A deliberate loop from observed behavior to trusted evaluations. Build your evidence, then raise the bar."
        action={
          <Link to="/import" className="button">
            <Upload size={16} />
            Import cases
          </Link>
        }
      />
      <section className="overview-intro">
        <div>
          <span className="small-label">YOUR QUALITY LOOP</span>
          <h2>
            Good answers are a start.
            <br />
            <em>Repeatable quality is the goal.</em>
          </h2>
          <p>
            Import scenarios, inspect your agent, and promote reviewed cases
            into immutable golden releases.
          </p>
          <TextLink to="/playground">Explore the agent</TextLink>
        </div>
        <div
          className="loop-diagram"
          aria-label="Observe, review, release, evaluate"
        >
          <span className="loop-center">
            g<span>l</span>
            <small>THE LOOP</small>
          </span>
          <span className="loop-node node-top">01 / Observe</span>
          <span className="loop-node node-right">02 / Review</span>
          <span className="loop-node node-bottom">03 / Release</span>
          <span className="loop-node node-left">04 / Evaluate</span>
        </div>
      </section>
      {summary.isPending ? (
        <Loading label="Loading workspace totals..." />
      ) : summary.isError ? (
        <ErrorState error={summary.error} retry={() => summary.refetch()} />
      ) : (
        <section className="metrics" aria-label="Workspace totals">
          {[
            {
              label: "Candidates",
              value: summary.data.candidates,
              note: "Review before release",
              to: "/candidates",
            },
            {
              label: "Golden releases",
              value: summary.data.releases,
              note: "Immutable snapshots",
              to: "/releases",
            },
            {
              label: "Evaluation runs",
              value: summary.data.runs,
              note: "Evidence across revisions",
              to: "/runs",
            },
            {
              label: "Feedback records",
              value: summary.data.feedback,
              note: "Signals, not ground truth",
              to: "/candidates?tab=feedback",
            },
          ].map((metric) => (
            <Link to={metric.to} className="metric" key={metric.label}>
              <div>
                <span>{metric.label}</span>
                <ArrowRight size={16} />
              </div>
              <strong>{metric.value.toLocaleString()}</strong>
              <small>{metric.note}</small>
            </Link>
          ))}
        </section>
      )}
      <section className="panel">
        <SectionHeading
          title="Recent evaluations"
          detail="Execution status and quality gates are separate signals."
          action={<TextLink to="/runs">All runs</TextLink>}
        />
        {runs.isPending ? (
          <Loading />
        ) : runs.isError ? (
          <ErrorState error={runs.error} retry={() => runs.refetch()} />
        ) : !runs.data.length ? (
          <EmptyState
            title="Your first run starts with a release"
            action={<TextLink to="/releases">Build a release</TextLink>}
          >
            Approve candidates and freeze a dataset before evaluating an agent.
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
                </tr>
              </thead>
              <tbody>
                {[...runs.data]
                  .sort((a, b) => b.created_at.localeCompare(a.created_at))
                  .slice(0, 5)
                  .map((run) => (
                    <tr key={run.id}>
                      <td>
                        <Link className="table-link" to={`/runs?run=${run.id}`}>
                          {run.release_name || run.release_id}
                        </Link>
                        <div className="cell-sub">
                          <ShortId value={run.id} />
                        </div>
                      </td>
                      <td>
                        <Status value={run.agent_revision} />
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
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <div className="overview-bottom">
        <section className="panel">
          <SectionHeading
            title="At the review desk"
            action={<TextLink to="/candidates">Review</TextLink>}
          />
          {cases.isPending ? (
            <Loading />
          ) : cases.isError ? (
            <ErrorState error={cases.error} retry={() => cases.refetch()} />
          ) : !cases.data.some((record) => record.status === "candidate") ? (
            <EmptyState title="No candidates waiting">
              Import cases or turn interaction feedback into a candidate.
            </EmptyState>
          ) : (
            <ul className="review-list">
              {cases.data
                .filter((record) => record.status === "candidate")
                .slice(0, 4)
                .map((record) => (
                  <li key={`${record.case.id}-${record.case.revision}`}>
                    <div>
                      <Link to={`/candidates?case=${record.case.id}`}>
                        {record.case.title}
                      </Link>
                      <small>
                        Revision {record.case.revision} /{" "}
                        {record.case.turns.length} turns /{" "}
                        {record.case.checks.length} checks
                      </small>
                    </div>
                    <Status value="candidate" />
                  </li>
                ))}
            </ul>
          )}
        </section>
        <aside className="principle">
          <span className="small-label">BUILT FOR TRUST</span>
          <h2>
            Nothing becomes
            <br />
            golden by accident.
          </h2>
          <p>
            Corrections are review signals. Approval is explicit. Every release
            preserves the cases and revisions that shaped its results.
          </p>
          <span className="synthetic-badge">SYNTHETIC FIXTURES</span>
          <p className="muted">
            This workbench does not imply a connected cloud deployment. Live
            runs require backend configuration.
          </p>
        </aside>
      </div>
    </>
  );
}
