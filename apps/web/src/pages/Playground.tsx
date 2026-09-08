import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Flag, MessageSquare, Plus, Send, Wrench } from "lucide-react";
import {
  api,
  type AgentRevision,
  type FeedbackInput,
  type Session,
  type ToolCall,
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
  Status,
  TextLink,
} from "../components";

type FeedbackTarget = Pick<FeedbackInput, "target" | "turn" | "tool_call_id">;
function FeedbackForm({
  sessionId,
  target,
  onClose,
}: {
  sessionId: string;
  target: FeedbackTarget;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const [issue, setIssue] = useState("");
  const [comment, setComment] = useState("");
  const [correction, setCorrection] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const save = useMutation({
    mutationFn: (input: FeedbackInput) => api.addFeedback(input),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["feedback"] });
      client.invalidateQueries({ queryKey: ["summary"] });
    },
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      let parsed: Record<string, unknown> | undefined;
      if (correction.trim()) {
        parsed = JSON.parse(correction);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
          throw new Error("Correction must be a JSON object.");
      }
      save.mutate({
        session_id: sessionId,
        ...target,
        issue_type: issue.trim(),
        comment: comment.trim(),
        ...(parsed ? { correction: parsed } : {}),
      });
    } catch (error) {
      setError(
        error instanceof SyntaxError
          ? new Error("Correction must be valid JSON.")
          : (error as Error),
      );
    }
  }
  return (
    <section className="feedback-compose">
      <SectionHeading
        title={`Feedback / ${target.target.replaceAll("_", " ")}`}
        detail={`Turn ${target.turn + 1}${target.tool_call_id ? ` / Call ${target.tool_call_id.slice(0, 8)}` : ""}`}
        action={
          <button className="button small secondary" onClick={onClose}>
            Close
          </button>
        }
      />
      {save.isSuccess ? (
        <div className="detail-body">
          <Notice tone="success">
            Feedback saved as a review signal, not a golden expectation.
          </Notice>
          <TextLink to="/candidates?tab=feedback">
            Review feedback and create a candidate
          </TextLink>
        </div>
      ) : (
        <form className="form-stack" onSubmit={submit}>
          <label>
            Issue type
            <input
              required
              list="issue-types"
              value={issue}
              onChange={(event) => setIssue(event.target.value)}
              placeholder="Choose or enter an issue type"
            />
            <datalist id="issue-types">
              <option value="incorrect_answer" />
              <option value="missing_tool" />
              <option value="unexpected_tool" />
              <option value="incorrect_arguments" />
              <option value="tool_error" />
              <option value="positive" />
              <option value="other" />
            </datalist>
          </label>
          <label>
            Reviewer comment
            <textarea
              required
              rows={3}
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              placeholder="Describe what you observed and what should be reviewed."
            />
          </label>
          <label>
            Proposed correction{" "}
            <span className="optional">optional JSON object, unreviewed</span>
            <textarea
              rows={3}
              className="code-editor"
              value={correction}
              onChange={(event) => setCorrection(event.target.value)}
              spellCheck={false}
              placeholder="{}"
            />
          </label>
          {(error || save.error) && <ErrorState error={error || save.error} />}
          <button
            className="button align-start"
            disabled={save.isPending || !comment.trim() || !issue.trim()}
          >
            {save.isPending ? "Saving..." : "Submit feedback"}
          </button>
        </form>
      )}
    </section>
  );
}

function ToolTrace({
  call,
  onFeedback,
}: {
  call: ToolCall;
  onFeedback: (target: FeedbackTarget) => void;
}) {
  return (
    <details className="tool-trace">
      <summary>
        <Wrench size={15} />
        <div>
          <strong>{call.tool}</strong>
          <span className="cell-sub">
            Turn {call.turn + 1} / {call.id.slice(0, 8)}
          </span>
        </div>
        <Status value={call.error ? "error" : "observed"} />
      </summary>
      <div className="trace-body">
        <span className="small-label">ARGUMENTS</span>
        <JsonView value={call.arguments} />
        <span className="small-label">RESULT</span>
        <JsonView value={call.result} />
        {call.error != null && (
          <>
            <span className="small-label">ERROR</span>
            <JsonView value={call.error} />
          </>
        )}
        <button
          className="button small secondary"
          onClick={() =>
            onFeedback({
              target: "tool",
              turn: call.turn,
              tool_call_id: call.id,
            })
          }
        >
          <Flag size={13} />
          Review this call
        </button>
      </div>
    </details>
  );
}

function Conversation({
  sessionId,
  onNewSession,
}: {
  sessionId: string;
  onNewSession: () => void;
}) {
  const client = useQueryClient();
  const [message, setMessage] = useState("");
  const [waitingAfter, setWaitingAfter] = useState<number | null>(null);
  const [feedbackTarget, setFeedbackTarget] = useState<FeedbackTarget | null>(
    null,
  );
  const session = useQuery({
    queryKey: ["session", sessionId],
    queryFn: ({ signal }) => api.session(sessionId, signal),
    refetchInterval: 2500,
  });
  const send = useMutation({
    mutationFn: (content: string) => api.sendMessage(sessionId, content),
    onSuccess: () => {
      setMessage("");
      client.invalidateQueries({ queryKey: ["session", sessionId] });
      client.invalidateQueries({ queryKey: ["sessions"] });
    },
    onError: () => setWaitingAfter(null),
  });
  const data = session.data;
  const assistantCount =
    data?.messages?.filter((item) => item.role === "assistant").length ?? 0;
  const executionFailed =
    !!data?.status &&
    ["failed", "error", "interrupted", "cancelled", "canceled"].includes(
      data.status,
    );
  const serverBusy =
    !!data?.status && ["queued", "pending", "running"].includes(data.status);
  const waiting =
    serverBusy ||
    (!executionFailed &&
      waitingAfter !== null &&
      assistantCount <= waitingAfter);
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!message.trim() || waiting || send.isPending || executionFailed) return;
    setWaitingAfter(assistantCount);
    send.mutate(message.trim());
  }
  if (session.isPending)
    return <Loading label="Loading conversation and trace..." />;
  if (!data)
    return <ErrorState error={session.error} retry={() => session.refetch()} />;
  const turns = [
    ...new Set(
      (data.messages ?? [])
        .filter((item) => item.role === "assistant")
        .map((item) => item.turn),
    ),
  ].sort((a, b) => a - b);
  return (
    <div className="conversation-workspace">
      <section className="panel conversation">
        <SectionHeading
          title={data.title || "Untitled session"}
          detail="Observed messages. No simulated token streaming."
          action={<Status value={data.agent_revision} />}
        />
        {session.isError && (
          <ErrorState error={session.error} retry={() => session.refetch()} />
        )}
        <div className="messages" aria-label="Conversation messages">
          {!data.messages?.length ? (
            <EmptyState
              title={
                executionFailed
                  ? "No completed messages"
                  : "Ask something worth testing"
              }
            >
              {executionFailed
                ? "This session cannot accept another turn. Start a new session to continue exploring."
                : "Send an input to the demo agent. Inspect its answer and observable tool behavior side by side."}
            </EmptyState>
          ) : (
            data.messages.map((item, index) => (
              <article
                className={`message ${item.role === "user" ? "user-message" : "agent-message"}`}
                key={`${item.turn}-${item.role}-${index}`}
              >
                <div className="message-meta">
                  <span>{item.role === "assistant" ? "Agent" : item.role}</span>
                  <small>Turn {item.turn + 1}</small>
                </div>
                <p>{item.content}</p>
                {item.role === "assistant" && (
                  <div className="message-actions">
                    <button
                      className="quiet-button"
                      onClick={() =>
                        setFeedbackTarget({ target: "answer", turn: item.turn })
                      }
                    >
                      <Flag size={13} />
                      Give answer feedback
                    </button>
                    <button
                      className="quiet-button"
                      onClick={() =>
                        setFeedbackTarget({
                          target: "missing_tool",
                          turn: item.turn,
                        })
                      }
                    >
                      Flag missing tool
                    </button>
                  </div>
                )}
              </article>
            ))
          )}
        </div>
        <div className="conversation-signals">
          <Status value={data.status} />
          <button
            className="quiet-button"
            onClick={() => session.refetch()}
            disabled={session.isFetching}
          >
            Refresh session
          </button>
        </div>
        {executionFailed && (
          <Notice tone="warning">
            The last chat execution {data.status}. Its trace is incomplete; this
            is not an agent-quality pass. This session cannot accept more
            messages.
            <button
              className="button small secondary"
              type="button"
              onClick={onNewSession}
            >
              Start a new session
            </button>
          </Notice>
        )}
        {data.error != null && (
          <div className="chat-execution-error">
            <span className="small-label">BACKEND EXECUTION ERROR</span>
            <JsonView value={data.error} />
          </div>
        )}
        {waiting && (
          <Notice>
            Message accepted or being submitted. Polling for the completed
            answer and trace; no streamed output is being simulated. If
            execution stalls, refresh the session to inspect its latest state.
          </Notice>
        )}
        {send.error && <ErrorState error={send.error} />}
        <form className="message-composer" onSubmit={submit}>
          <label htmlFor="chat-message">Your next turn</label>
          <textarea
            id="chat-message"
            rows={3}
            required
            placeholder="Ask the agent a question..."
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            disabled={send.isPending || waiting || executionFailed}
          />
          <div>
            <span className="field-hint">
              Synthetic inputs only. Do not enter sensitive data.
            </span>
            <button
              className="button"
              disabled={
                !message.trim() || send.isPending || waiting || executionFailed
              }
            >
              <Send size={15} />
              {send.isPending
                ? "Sending..."
                : waiting
                  ? "Awaiting agent..."
                  : "Send message"}
            </button>
          </div>
        </form>
      </section>
      <aside className="trace-column">
        <section className="panel">
          <SectionHeading
            title="Observable trace"
            detail="Tool calls, not hidden reasoning."
          />
          <div className="trace-status">
            <Status
              value={
                data.trace_complete === true
                  ? "complete"
                  : data.trace_complete === false
                    ? "incomplete"
                    : undefined
              }
            />
            <span className="muted">Trace completeness</span>
          </div>
          {!data.tool_calls?.length ? (
            <EmptyState title="No tool calls observed">
              An absent trace is not proof of correct tool behavior. Flag a
              missing tool if the task required one.
            </EmptyState>
          ) : (
            data.tool_calls.map((call) => (
              <ToolTrace
                key={call.id}
                call={call}
                onFeedback={setFeedbackTarget}
              />
            ))
          )}
          {turns.length > 0 && (
            <div className="detail-body">
              <label>
                Flag a missing tool for a turn
                <select
                  value=""
                  onChange={(event) => {
                    if (event.target.value !== "")
                      setFeedbackTarget({
                        target: "missing_tool",
                        turn: Number(event.target.value),
                      });
                  }}
                >
                  <option value="">Select a turn</option>
                  {turns.map((turn) => (
                    <option key={turn} value={turn}>
                      Turn {turn + 1}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          )}
          <div className="telemetry-note">
            <span>
              Latency <strong>Not reported</strong>
            </span>
            <span>
              Token usage <strong>Not reported</strong>
            </span>
          </div>
        </section>
        {feedbackTarget && (
          <FeedbackForm
            key={`${feedbackTarget.target}-${feedbackTarget.turn}-${feedbackTarget.tool_call_id || ""}`}
            sessionId={sessionId}
            target={feedbackTarget}
            onClose={() => setFeedbackTarget(null)}
          />
        )}
      </aside>
    </div>
  );
}

export default function Playground() {
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const selected = params.get("session");
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [revision, setRevision] = useState<AgentRevision>("fixed");
  const sessions = useQuery({
    queryKey: ["sessions"],
    queryFn: ({ signal }) => api.sessions(signal),
  });
  const create = useMutation({
    mutationFn: () => api.createSession(title.trim(), revision),
    onSuccess: (session: Session) => {
      client.invalidateQueries({ queryKey: ["sessions"] });
      setParams({ session: session.id });
      setCreating(false);
      setTitle("");
    },
  });
  return (
    <>
      <PageHeader
        eyebrow="02 / OBSERVE THE AGENT"
        title="Follow the conversation."
        description="Explore deterministic demo-agent revisions. Inspect the answer, examine the tools, and turn observations into reviewable feedback."
        action={
          <button
            className="button"
            onClick={() => setCreating((value) => !value)}
          >
            <Plus size={16} />
            New session
          </button>
        }
      />
      <Notice>
        Demo sessions use synthetic fixtures. The agent revision is fixed for
        each session; a cloud connection is not implied.
      </Notice>
      {creating && (
        <section className="panel">
          <SectionHeading title="Start an exploration" />
          <form
            className="form-stack"
            onSubmit={(event) => {
              event.preventDefault();
              create.mutate();
            }}
          >
            <div className="form-row">
              <label>
                Session title <span className="optional">optional</span>
                <input
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  placeholder="What are you investigating?"
                />
              </label>
              <label>
                Demo-agent revision
                <select
                  value={revision}
                  onChange={(event) =>
                    setRevision(event.target.value as AgentRevision)
                  }
                >
                  <option value="fixed">Fixed</option>
                  <option value="buggy">Buggy</option>
                </select>
              </label>
            </div>
            {create.error && <ErrorState error={create.error} />}
            <div className="form-actions">
              <button className="button" disabled={create.isPending}>
                {create.isPending ? "Creating..." : "Create session"}
              </button>
              <button
                type="button"
                className="button secondary"
                onClick={() => setCreating(false)}
              >
                Cancel
              </button>
            </div>
          </form>
        </section>
      )}
      <section className="session-selector">
        {sessions.isPending ? (
          <Loading label="Loading sessions..." />
        ) : sessions.isError ? (
          <ErrorState error={sessions.error} retry={() => sessions.refetch()} />
        ) : !sessions.data.length ? (
          <EmptyState
            title="A clean conversation slate"
            action={
              <button
                className="button secondary"
                onClick={() => setCreating(true)}
              >
                <MessageSquare size={16} />
                Start a session
              </button>
            }
          >
            No sessions have been created. Choose a revision to begin.
          </EmptyState>
        ) : (
          <>
            <label>
              Conversation
              <select
                value={selected || ""}
                onChange={(event) =>
                  setParams(
                    event.target.value ? { session: event.target.value } : {},
                  )
                }
              >
                <option value="">Select a session</option>
                {sessions.data.map((session) => (
                  <option key={session.id} value={session.id}>
                    {session.title || "Untitled session"} /{" "}
                    {session.agent_revision || "revision not reported"} /{" "}
                    {session.id.slice(0, 8)}
                  </option>
                ))}
              </select>
            </label>
            {selected && (
              <span className="muted">
                Created{" "}
                <DateLabel
                  value={
                    sessions.data.find((session) => session.id === selected)
                      ?.created_at
                  }
                />
              </span>
            )}
          </>
        )}
      </section>
      {selected ? (
        <Conversation
          key={selected}
          sessionId={selected}
          onNewSession={() => {
            setCreating(true);
            setParams({});
          }}
        />
      ) : (
        !!sessions.data?.length && (
          <EmptyState title="Pick up a thread">
            Select a saved session above or start a new exploration.
          </EmptyState>
        )
      )}
    </>
  );
}
