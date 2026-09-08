import {
  startTransition,
  useDeferredValue,
  useState,
  type FormEvent,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Check, Code2, Plus, Search, X } from "lucide-react";
import {
  api,
  type CaseRecord,
  type CanonicalCase,
  type Feedback,
} from "../api";
import {
  blankCanonical,
  buildCase,
  emptyFields,
  parseCase,
  type SimpleCaseFields,
} from "../case-form";
import {
  EmptyState,
  ErrorState,
  JsonView,
  Loading,
  Notice,
  PageHeader,
  SectionHeading,
  ShortId,
  Status,
} from "../components";

export function CaseEditor({
  record,
  onSaved,
  onClose,
}: {
  record?: CaseRecord;
  onSaved: (record: CaseRecord) => void;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const [advanced, setAdvanced] = useState(!!record);
  const [fields, setFields] = useState<SimpleCaseFields>(emptyFields);
  const [text, setText] = useState(
    JSON.stringify(record?.case ?? blankCanonical, null, 2),
  );
  const [reason, setReason] = useState("");
  const [formError, setFormError] = useState<Error | null>(null);
  const save = useMutation({
    mutationFn: (value: CanonicalCase) =>
      record?.case.id
        ? api.updateCase(
            record.case.id,
            value,
            record.case.revision,
            reason.trim(),
          )
        : api.createCase(value, reason.trim() || undefined),
    onSuccess: (saved) => {
      client.invalidateQueries({ queryKey: ["cases"] });
      client.invalidateQueries({ queryKey: ["case", saved.case.id] });
      client.invalidateQueries({ queryKey: ["summary"] });
      onSaved(saved);
    },
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    setFormError(null);
    try {
      const value = advanced ? parseCase(text) : buildCase(fields);
      if (
        record &&
        (!reason.trim() ||
          value.id !== record.case.id ||
          value.revision !== record.case.revision)
      )
        throw new Error(
          "Keep the existing case ID and revision, and give a reason. The server creates the new revision.",
        );
      save.mutate(value);
    } catch (error) {
      setFormError(error as Error);
    }
  }
  function openAdvanced() {
    try {
      setText(JSON.stringify(buildCase(fields), null, 2));
      setAdvanced(true);
      setFormError(null);
    } catch (error) {
      setFormError(error as Error);
    }
  }
  const field = (name: keyof SimpleCaseFields, value: string) =>
    setFields((previous) => ({ ...previous, [name]: value }));
  return (
    <section className="panel editor-panel">
      <SectionHeading
        title={
          record
            ? `Edit revision ${record.case.revision}`
            : "Create a candidate"
        }
        detail={
          record
            ? "Saving creates a new candidate revision. Existing releases and results stay unchanged."
            : "Expectations are authored by you, not inferred from an agent answer."
        }
        action={
          <button
            className="icon-button"
            onClick={onClose}
            aria-label="Close case editor"
          >
            <X size={19} />
          </button>
        }
      />
      <form className="form-stack" onSubmit={submit}>
        {!advanced ? (
          <>
            <div className="form-row">
              <label>
                Case title
                <input
                  required
                  value={fields.title}
                  onChange={(event) => field("title", event.target.value)}
                  placeholder="Describe the behavior to verify"
                />
              </label>
              <label>
                Tags <span className="optional">optional, comma separated</span>
                <input
                  value={fields.tags}
                  onChange={(event) => field("tags", event.target.value)}
                />
              </label>
            </div>
            <label>
              User message
              <textarea
                required
                rows={3}
                value={fields.user}
                onChange={(event) => field("user", event.target.value)}
                placeholder="The input the agent will receive"
              />
            </label>
            <label>
              Reference answer{" "}
              <span className="optional">optional, reviewer expectation</span>
              <textarea
                rows={3}
                value={fields.reference}
                onChange={(event) => field("reference", event.target.value)}
              />
            </label>
            <label>
              Context <span className="optional">optional</span>
              <textarea
                rows={2}
                value={fields.context}
                onChange={(event) => field("context", event.target.value)}
              />
            </label>
            <fieldset>
              <legend>Deterministic expectations / turn 1</legend>
              <div className="form-row">
                <label>
                  Content check
                  <select
                    value={fields.contentKind}
                    onChange={(event) =>
                      field("contentKind", event.target.value)
                    }
                  >
                    <option value="content_contains">Contains</option>
                    <option value="content_excludes">Does not contain</option>
                    <option value="exact_match">Exactly matches</option>
                  </select>
                </label>
                <label>
                  Expected content{" "}
                  <span className="optional">leave blank to skip</span>
                  <input
                    value={fields.content}
                    onChange={(event) => field("content", event.target.value)}
                  />
                </label>
              </div>
              <label>
                Required tool <span className="optional">exact tool name</span>
                <input
                  value={fields.tool}
                  onChange={(event) => field("tool", event.target.value)}
                  placeholder="Leave blank for no tool check"
                />
              </label>
              <div className="form-row">
                <label>
                  Argument path <span className="optional">optional</span>
                  <input
                    value={fields.argumentPath}
                    onChange={(event) =>
                      field("argumentPath", event.target.value)
                    }
                    placeholder="Argument path understood by the SDK"
                  />
                </label>
                <label>
                  Argument equals <span className="optional">JSON value</span>
                  <input
                    value={fields.argumentValue}
                    onChange={(event) =>
                      field("argumentValue", event.target.value)
                    }
                    placeholder={'"expected string"'}
                  />
                </label>
              </div>
              <p className="field-hint">
                These checks are required and apply to the first turn. Use JSON
                for optional checks, other turns, tool order, schemas, or
                judges.
              </p>
            </fieldset>
            <div className="form-actions">
              <button
                type="button"
                className="button secondary align-start"
                onClick={openAdvanced}
              >
                <Code2 size={16} />
                Continue in canonical JSON
              </button>
              {!fields.title && !fields.user && (
                <button
                  type="button"
                  className="quiet-button"
                  onClick={() => setAdvanced(true)}
                >
                  Start directly with JSON
                </button>
              )}
            </div>
          </>
        ) : (
          <>
            <label>
              Canonical case JSON
              <textarea
                className="code-editor"
                rows={22}
                spellCheck={false}
                required
                value={text}
                onChange={(event) => setText(event.target.value)}
              />
            </label>
            <details className="help-details">
              <summary>Supported checks and JSON conventions</summary>
              <p>
                Turns use{" "}
                <code>{'{"user":"...","reference_answer":"..."}'}</code>. A
                check has <code>kind</code>, <code>required</code>,{" "}
                <code>turn</code> (null or zero-based), and <code>config</code>.
                Check IDs are optional.
              </p>
              <JsonView
                value={{
                  content_contains: { value: "text" },
                  content_excludes: { value: "text" },
                  exact_match: { value: "text" },
                  tool_required: { tool: "tool_name", min: 1 },
                  tool_forbidden: { tool: "tool_name" },
                  tool_arguments: {
                    tool: "tool_name",
                    path: "argument",
                    operator: "equals",
                    value: "expected",
                  },
                  tool_order: { tools: ["first_tool", "second_tool"] },
                  json_schema: { schema: { type: "object" } },
                  judge: {
                    rubric: "Reviewer-defined criteria",
                    threshold: 0.8,
                  },
                }}
              />
              <p>
                Examples explain configuration only; they are not saved checks.
                Judge checks require a configured backend judge. No judge result
                is simulated by this UI.
              </p>
            </details>
          </>
        )}
        <label>
          {record ? "Revision reason" : "Authoring note"}
          {!record && <span className="optional"> optional</span>}
          <input
            required={!!record}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </label>
        {(formError || save.error) && (
          <ErrorState error={formError || save.error} />
        )}
        <div className="form-actions">
          <button className="button" disabled={save.isPending}>
            {save.isPending
              ? "Saving..."
              : record
                ? "Save new candidate revision"
                : "Save candidate"}
          </button>
          <button className="button secondary" type="button" onClick={onClose}>
            Cancel
          </button>
          <span className="field-hint">Saving does not approve this case.</span>
        </div>
      </form>
    </section>
  );
}

function CaseReview({
  caseId,
  onEdit,
}: {
  caseId: string;
  onEdit: (record: CaseRecord) => void;
}) {
  const client = useQueryClient();
  const [reason, setReason] = useState("");
  const [confirmedRevision, setConfirmedRevision] = useState<number | null>(null);
  const detail = useQuery({
    queryKey: ["case", caseId],
    queryFn: ({ signal }) => api.case(caseId, signal),
  });
  const approve = useMutation({
    mutationFn: () => {
      if (confirmedRevision !== detail.data?.case.revision || !reason.trim()) {
        throw new Error("Review and confirm the current case revision before approval.");
      }
      return api.approve(caseId, confirmedRevision, reason.trim());
    },
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["case", caseId] });
      client.invalidateQueries({ queryKey: ["cases"] });
      client.invalidateQueries({ queryKey: ["summary"] });
      setReason("");
      setConfirmedRevision(null);
    },
  });
  if (detail.isPending) return <Loading />;
  if (detail.isError)
    return <ErrorState error={detail.error} retry={() => detail.refetch()} />;
  const record = detail.data;
  return (
    <section className="panel">
      <SectionHeading
        title={record.case.title}
        detail={`Revision ${record.case.revision} / ${record.case.fixture_version}`}
        action={
          <button
            className="button small secondary"
            onClick={() => onEdit(record)}
          >
            Edit as new revision
          </button>
        }
      />
      <div className="detail-body">
        <div className="inline-meta">
          <Status value={record.status} />
          <ShortId value={caseId} />
          {record.case.tags.map((tag) => (
            <span className="tag" key={tag}>
              {tag}
            </span>
          ))}
        </div>
        {record.case.context && (
          <div className="prose-block">
            <span className="small-label">CONTEXT</span>
            <p>{record.case.context}</p>
          </div>
        )}
        {record.case.turns.map((turn, index) => (
          <div className="turn-review" key={index}>
            <span className="small-label">TURN {index + 1}</span>
            <p>{turn.user}</p>
            <div className="reference">
              <span className="small-label">REVIEWER REFERENCE</span>
              <p>{turn.reference_answer || "No reference answer authored."}</p>
            </div>
          </div>
        ))}
        <h3>Authored checks</h3>
        {!record.case.checks.length ? (
          <Notice tone="warning">
            No checks authored. Review the evaluation requirements before
            approval.
          </Notice>
        ) : (
          record.case.checks.map((check, index) => (
            <details className="check-details" key={check.id || index}>
              <summary>
                <code>{check.kind}</code>
                <span className="muted">
                  {check.required ? "Required" : "Optional"} /{" "}
                  {check.turn === null ? "All turns" : `Turn ${check.turn + 1}`}
                </span>
              </summary>
              <JsonView value={check.config} />
            </details>
          ))
        )}
        <details className="help-details">
          <summary>Canonical case and provenance</summary>
          <JsonView value={record.case} />
        </details>
        {record.reason && (
          <p className="muted">Review / revision note: {record.reason}</p>
        )}
        {record.reviewer && (
          <p className="muted">Reviewer: {record.reviewer}</p>
        )}
        {record.status === "candidate" ? (
          <form
            className="approval-box form-stack"
            onSubmit={(event) => {
              event.preventDefault();
              approve.mutate();
            }}
          >
            <h3>Promote deliberately.</h3>
            <p>
              Approval makes this revision eligible for a golden release. It
              does not change any existing release.
            </p>
            <label>
              Approval reason
              <input
                required
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            </label>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={confirmedRevision === record.case.revision}
                onChange={(event) => setConfirmedRevision(event.target.checked ? record.case.revision : null)}
                required
              />
              I reviewed the inputs, references, and check expectations.
            </label>
            {approve.error && <ErrorState error={approve.error} />}
            <button
              className="button align-start"
              disabled={approve.isPending || confirmedRevision !== record.case.revision || !reason.trim() || detail.isFetching}
            >
              <Check size={16} />
              {approve.isPending
                ? "Approving..."
                : `Approve revision ${record.case.revision}`}
            </button>
          </form>
        ) : (
          <Notice tone="success">
            This revision is approved and eligible for release. Changes must
            create a new candidate revision.
          </Notice>
        )}
      </div>
    </section>
  );
}

function FeedbackReview({ feedback }: { feedback: Feedback }) {
  const client = useQueryClient();
  const [status, setStatus] = useState<"accepted" | "rejected" | "unresolved">(
    feedback.status || "unresolved",
  );
  const [reason, setReason] = useState("");
  const [notice, setNotice] = useState("");
  const review = useMutation({
    mutationFn: () => api.reviewFeedback(feedback.id, status, reason.trim()),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["feedback"] });
      setNotice(
        "Feedback review saved. Case approval remains a separate action.",
      );
    },
  });
  const candidate = useMutation({
    mutationFn: () => api.feedbackCandidate(feedback.id),
    onSuccess: (record) => {
      client.invalidateQueries({ queryKey: ["cases"] });
      client.invalidateQueries({ queryKey: ["summary"] });
      setNotice(
        `Candidate created: ${record.case.title}. Open Candidates to author and review its expectations.`,
      );
    },
  });
  return (
    <details className="feedback-item">
      <summary>
        <div>
          <strong>{feedback.issue_type.replaceAll("_", " ")}</strong>
          <span className="cell-sub">
            {feedback.target.replaceAll("_", " ")} / Turn {feedback.turn + 1} /
            Session {feedback.session_id.slice(0, 8)}
          </span>
        </div>
        <Status value={feedback.status || "unresolved"} />
      </summary>
      <div className="detail-body">
        <p className="preserve-lines">{feedback.comment}</p>
        {feedback.tool_call_id && (
          <p className="muted">
            Tool call: <code>{feedback.tool_call_id}</code>
          </p>
        )}
        {feedback.correction && (
          <>
            <span className="small-label">
              PROPOSED CORRECTION / NOT GROUND TRUTH
            </span>
            <JsonView value={feedback.correction} />
          </>
        )}
        {feedback.reason && <p>Review reason: {feedback.reason}</p>}
        <form
          className="form-stack"
          onSubmit={(event) => {
            event.preventDefault();
            review.mutate();
          }}
        >
          <div className="form-row">
            <label>
              Review decision
              <select
                value={status}
                onChange={(event) =>
                  setStatus(event.target.value as typeof status)
                }
              >
                <option value="unresolved">Unresolved</option>
                <option value="accepted">Accepted feedback</option>
                <option value="rejected">Rejected feedback</option>
              </select>
            </label>
            <label>
              Decision reason
              <input
                required
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            </label>
          </div>
          <div className="form-actions">
            <button
              className="button secondary"
              disabled={review.isPending || !reason.trim()}
            >
              {review.isPending ? "Saving..." : "Save feedback review"}
            </button>
            <button
              type="button"
              className="button"
              disabled={candidate.isPending || candidate.isSuccess}
              onClick={() => candidate.mutate()}
            >
              {candidate.isPending
                ? "Creating..."
                : candidate.isSuccess
                  ? "Candidate created"
                  : "Create unreviewed candidate"}
            </button>
          </div>
        </form>
        <p className="field-hint">
          Creating a candidate does not copy corrections into automatic
          expectations or approve it.
        </p>
        {notice && <Notice tone="success">{notice}</Notice>}
        {(review.error || candidate.error) && (
          <ErrorState error={review.error || candidate.error} />
        )}
      </div>
    </details>
  );
}

export default function Candidates() {
  const [params, setParams] = useSearchParams();
  const feedbackTab = params.get("tab") === "feedback";
  const selected = params.get("case");
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);
  const [filter, setFilter] = useState("all");
  const [editor, setEditor] = useState<CaseRecord | "new" | null>(null);
  const [notice, setNotice] = useState("");
  const cases = useQuery({
    queryKey: ["cases"],
    queryFn: ({ signal }) => api.cases(signal),
  });
  const feedback = useQuery({
    queryKey: ["feedback"],
    queryFn: ({ signal }) => api.feedback(signal),
    enabled: feedbackTab,
  });
  const records =
    cases.data?.filter(
      (record) =>
        (filter === "all" || record.status === filter) &&
        `${record.case.title} ${record.case.tags.join(" ")} ${record.case.id}`
          .toLowerCase()
          .includes(deferredSearch.toLowerCase()),
    ) ?? [];
  return (
    <>
      <PageHeader
        eyebrow="THE REVIEW DESK"
        title="Nothing golden without review."
        description="Separate observed behavior from authored expectations. Review feedback, refine cases, and explicitly approve a revision."
        action={
          <button
            className="button"
            onClick={() => {
              setEditor("new");
              setNotice("");
            }}
          >
            <Plus size={16} />
            New candidate
          </button>
        }
      />
      <div className="tabs" aria-label="Review views">
        <button
          aria-pressed={!feedbackTab}
          className={!feedbackTab ? "active" : ""}
          onClick={() => setParams({})}
        >
          Candidates & approvals
        </button>
        <button
          aria-pressed={feedbackTab}
          className={feedbackTab ? "active" : ""}
          onClick={() => setParams({ tab: "feedback" })}
        >
          Interaction feedback
        </button>
      </div>
      {notice && <Notice tone="success">{notice}</Notice>}
      {editor && (
        <CaseEditor
          key={
            editor === "new"
              ? "new"
              : `${editor.case.id}-${editor.case.revision}`
          }
          record={editor === "new" ? undefined : editor}
          onClose={() => setEditor(null)}
          onSaved={(record) => {
            setEditor(null);
            setNotice(
              "Candidate saved. Review and approve it explicitly before release.",
            );
            setParams(record.case.id ? { case: record.case.id } : {});
          }}
        />
      )}
      {feedbackTab ? (
        <section className="panel">
          <SectionHeading
            title="Signals from real interactions"
            detail="Accepting feedback is not the same as approving a golden case."
          />
          {feedback.isPending ? (
            <Loading />
          ) : feedback.isError ? (
            <ErrorState
              error={feedback.error}
              retry={() => feedback.refetch()}
            />
          ) : !feedback.data.length ? (
            <EmptyState title="No feedback to review">
              Explore the agent in Playground and annotate an answer, a tool
              call, or a missing tool.
            </EmptyState>
          ) : (
            feedback.data.map((item) => (
              <FeedbackReview key={item.id} feedback={item} />
            ))
          )}
        </section>
      ) : (
        <>
          <section className="panel">
            <div className="table-toolbar">
              <label className="search-field">
                <Search size={17} />
                <span className="sr-only">Search cases</span>
                <input
                  placeholder="Search titles, tags, IDs..."
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </label>
              <label className="filter-label">
                Status
                <select
                  value={filter}
                  onChange={(event) => setFilter(event.target.value)}
                >
                  <option value="all">All statuses</option>
                  <option value="candidate">Candidate</option>
                  <option value="approved">Approved</option>
                </select>
              </label>
            </div>
            {cases.isPending ? (
              <Loading />
            ) : cases.isError ? (
              <ErrorState error={cases.error} retry={() => cases.refetch()} />
            ) : !records.length ? (
              <EmptyState
                title={
                  cases.data.length
                    ? "No matching cases"
                    : "The review desk is clear"
                }
              >
                {cases.data.length
                  ? "Change the search or status filter."
                  : "Import a file, create a candidate, or capture feedback in Playground."}
              </EmptyState>
            ) : (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Case</th>
                      <th>Revision</th>
                      <th>Turns / checks</th>
                      <th>Status</th>
                      <th>
                        <span className="sr-only">Action</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {records.map((record) => (
                      <tr
                        className={
                          selected === record.case.id ? "selected-row" : ""
                        }
                        key={`${record.case.id}-${record.case.revision}`}
                      >
                        <td>
                          <strong>{record.case.title}</strong>
                          <div className="cell-sub">
                            {record.case.tags.join(" / ") ||
                              record.case.fixture_version}
                          </div>
                        </td>
                        <td>
                          <span className="mono">r{record.case.revision}</span>
                        </td>
                        <td>
                          {record.case.turns.length} /{" "}
                          {record.case.checks.length}
                        </td>
                        <td>
                          <Status value={record.status} />
                        </td>
                        <td>
                          <button
                            className="button small secondary"
                            disabled={!record.case.id}
                            onClick={() =>
                              startTransition(() =>
                                setParams({ case: record.case.id! }),
                              )
                            }
                          >
                            Review
                            <span className="sr-only">
                              {" "}
                              {record.case.title}
                            </span>
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
          {selected && !editor && (
            <CaseReview key={selected} caseId={selected} onEdit={setEditor} />
          )}
        </>
      )}
    </>
  );
}
