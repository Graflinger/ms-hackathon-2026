import type { components } from "./generated/api-schema";

type Schemas = components["schemas"];
export type JsonObject = Record<string, unknown>;
export type CreateCase = Schemas["CreateCase"];
export type EditCase = Schemas["EditCase"];
export type CreateRelease = Schemas["CreateRelease"];
export type CreateRun = Schemas["CreateRun"];
export type AgentRevision = CreateRun["agent_revision"];
export type Mode = NonNullable<CreateRun["mode"]>;
export type Judge = NonNullable<CreateRun["judge"]>;
export type Health = Schemas["Health"];
export type Summary = Schemas["Summary"];
export type CheckKind =
  | "content_contains"
  | "content_excludes"
  | "exact_match"
  | "tool_required"
  | "tool_forbidden"
  | "tool_arguments"
  | "tool_order"
  | "json_schema"
  | "judge";
// The editor materializes defaults but allows the API to assign case/check IDs.
export type Check = Required<Omit<Schemas["Check"], "id">> &
  Pick<Schemas["Check"], "id">;
export type CanonicalCase = Required<Omit<CreateCase["case"], "id" | "checks">> &
  Pick<CreateCase["case"], "id"> & { checks: Check[] };
export interface CaseRecord extends Omit<Schemas["CaseRecord"], "case"> {
  case: CanonicalCase;
}
export type ImportPreview = Schemas["ImportPreview"];
export interface ImportCommit extends Omit<Schemas["ImportCommit"], "cases"> {
  cases: CaseRecord[];
}
export type Release = Schemas["ReleaseRecord"];
export interface ReleaseDetail extends Omit<Schemas["ReleaseDetail"], "cases"> {
  cases: CanonicalCase[];
}
export type Message = Schemas["Message"];
export type ToolCall = Required<Schemas["ToolCall"]>;
export type Session = Schemas["ChatRecord"] &
  Partial<Pick<Schemas["ChatDetail"], "messages">> & { tool_calls?: ToolCall[] };
export type Feedback = Schemas["FeedbackRecord"];
export type FeedbackInput = Schemas["CreateFeedback"];
export type CheckResult = Schemas["CheckResult"];
export type CaseResult = Schemas["CaseResult"];
export type Run = Schemas["RunRecord"];
export type RunDetail = Schemas["RunDetail"];

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...init,
      headers: {
        ...(init?.body && !(init.body instanceof FormData)
          ? { "Content-Type": "application/json" }
          : {}),
        ...init?.headers,
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new ApiError(
      0,
      "Cannot reach the API. Check that the backend is running and try again.",
    );
  }
  if (!response.ok) {
    const text = await response.text();
    let message = text || response.statusText;
    try {
      const body = JSON.parse(text);
      message =
        typeof body.detail === "string"
          ? body.detail
          : JSON.stringify(body.detail ?? body);
    } catch {
      /* Non-JSON errors may come from the development proxy. */
    }
    throw new ApiError(response.status, `${response.status}: ${message}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});
const id = encodeURIComponent;
export const api = {
  health: (signal?: AbortSignal) => request<Health>("/health", { signal }),
  summary: (signal?: AbortSignal) => request<Summary>("/summary", { signal }),
  cases: (signal?: AbortSignal) => request<CaseRecord[]>("/cases", { signal }),
  case: (caseId: string, signal?: AbortSignal) =>
    request<CaseRecord>(`/cases/${id(caseId)}`, { signal }),
  createCase: (value: CanonicalCase, reason?: string) =>
    request<CaseRecord>("/cases", json("POST", { case: value, reason } satisfies CreateCase)),
  updateCase: (
    caseId: string,
    value: CanonicalCase,
    expected_revision: number,
    reason: string,
  ) =>
    request<CaseRecord>(
      `/cases/${id(caseId)}`,
      json("PUT", { case: value, expected_revision, reason } satisfies EditCase),
    ),
  approve: (caseId: string, revision: number, reason: string) =>
    request<CaseRecord>(
      `/cases/${id(caseId)}/approve`,
      json("POST", { revision, reason } satisfies Schemas["Approve"]),
    ),
  preview: (file: File, sheet?: string) => {
    const body = new FormData();
    body.append("file", file);
    if (sheet) body.append("sheet", sheet);
    return request<ImportPreview>("/imports/preview", { method: "POST", body });
  },
  commitImport: (
    importId: string,
    mapping: Record<string, string>,
    duplicate_policy: "new" | "reject",
    sheet?: string,
  ) =>
    request<ImportCommit>(
      `/imports/${id(importId)}/commit`,
      json("POST", { mapping, duplicate_policy, ...(sheet ? { sheet } : {}) } satisfies Schemas["CommitImport"]),
    ),
  releases: (signal?: AbortSignal) =>
    request<Release[]>("/dataset-releases", { signal }),
  release: (releaseId: string, signal?: AbortSignal) =>
    request<ReleaseDetail>(`/dataset-releases/${id(releaseId)}`, { signal }),
  createRelease: (
    name: string,
    case_ids: string[],
    expected_revisions: Record<string, number>,
  ) =>
    request<Release>(
      "/dataset-releases",
      json("POST", { name, case_ids, expected_revisions } satisfies CreateRelease),
    ),
  exportUrl: (releaseId: string) =>
    `/api/v1/dataset-releases/${id(releaseId)}/export`,
  sessions: (signal?: AbortSignal) =>
    request<Session[]>("/chat-sessions", { signal }),
  createSession: (title: string, agent_revision: AgentRevision) =>
    request<Session>(
      "/chat-sessions",
      json("POST", { title: title || undefined, agent_revision } satisfies Schemas["CreateChat"]),
    ),
  session: (sessionId: string, signal?: AbortSignal) =>
    request<Session>(`/chat-sessions/${id(sessionId)}`, { signal }),
  sendMessage: (sessionId: string, content: string) =>
    request<Schemas["MessageAccepted"]>(
      `/chat-sessions/${id(sessionId)}/messages`,
      json("POST", { content } satisfies Schemas["SendMessage"]),
    ),
  feedback: (signal?: AbortSignal) =>
    request<Feedback[]>("/feedback", { signal }),
  addFeedback: (input: FeedbackInput) =>
    request<Feedback>("/feedback", json("POST", input)),
  feedbackCandidate: (feedbackId: string) =>
    request<CaseRecord>(`/feedback/${id(feedbackId)}/candidate`, json("POST")),
  reviewFeedback: (
    feedbackId: string,
    status: "accepted" | "rejected" | "unresolved",
    reason: string,
  ) =>
    request<Feedback>(
      `/feedback/${id(feedbackId)}/review`,
      json("POST", { status, reason } satisfies Schemas["ReviewFeedback"]),
    ),
  runs: (signal?: AbortSignal) =>
    request<Run[]>("/evaluation-runs", { signal }),
  run: (runId: string, signal?: AbortSignal) =>
    request<RunDetail>(`/evaluation-runs/${id(runId)}`, { signal }),
  startRun: (
    release_id: string,
    agent_revision: AgentRevision,
    mode: Mode,
    idempotency_key: string,
    judge: Judge = "none",
  ) =>
    request<Run>(
      "/evaluation-runs",
      json("POST", {
        release_id,
        agent_revision,
        mode,
        idempotency_key,
        judge,
      } satisfies CreateRun),
    ),
  cancelRun: (runId: string) =>
    request<RunDetail>(`/evaluation-runs/${id(runId)}/cancel`, json("POST")),
};

export function isActiveRun(run: Run): boolean {
  return [
    "pending",
    "queued",
    "running",
    "cancelling",
    "cancel_requested",
  ].includes(run.status);
}
