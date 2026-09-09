import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import { api, TestProject, execution, mockRegistry } from "./project-fixtures";
import { EmptyState, ErrorState, Status } from "../components";
import Overview from "../pages/Overview";
import { CaseEditor } from "../pages/Candidates";
import Candidates from "../pages/Candidates";
import Playground from "../pages/Playground";
import Releases from "../pages/Releases";
import { blankCanonical } from "../case-form";

function renderWithClient(ui: React.ReactNode, route = "/") {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <TestProject>{ui}</TestProject>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, client };
}

describe("accessible data states", () => {
  it("exposes an actionable error alert", async () => {
    const retry = vi.fn();
    render(
      <ErrorState error={new Error("Backend unavailable")} retry={retry} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Backend unavailable");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledOnce();
  });
  it("shows an explanatory empty state", () => {
    render(
      <EmptyState title="No releases">Approve a candidate first.</EmptyState>,
    );
    expect(screen.getByRole("heading", { name: "No releases" })).toBeVisible();
    expect(screen.getByText("Approve a candidate first.")).toBeVisible();
  });
  it("does not turn missing gates into passes", () => {
    render(
      <>
        <Status />
        <Status value="incomplete" />
        <Status value="failed" />
      </>,
    );
    expect(screen.getByText("not reported")).toBeVisible();
    expect(screen.getByText("incomplete")).toHaveClass("warn");
    expect(screen.getByText("failed")).toHaveClass("bad");
    expect(screen.queryByText("passed")).not.toBeInTheDocument();
  });
});

describe("overview API integration", () => {
  it("renders real zero totals and empty lists", async () => {
    vi.spyOn(api, "summary").mockResolvedValue({
      candidates: 0,
      releases: 0,
      runs: 0,
      feedback: 0,
    });
    vi.spyOn(api, "runs").mockResolvedValue([]);
    vi.spyOn(api, "cases").mockResolvedValue([]);
    renderWithClient(<Overview />);
    expect(
      await screen.findByText("Your first run starts with a release"),
    ).toBeVisible();
    expect(screen.getAllByText("0")).toHaveLength(4);
    expect(screen.getByText("No candidates waiting")).toBeVisible();
  });
  it("does not fabricate totals when the summary fails", async () => {
    vi.spyOn(api, "summary").mockRejectedValue(
      new Error("Summary unavailable"),
    );
    vi.spyOn(api, "runs").mockResolvedValue([]);
    vi.spyOn(api, "cases").mockResolvedValue([]);
    renderWithClient(<Overview />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Summary unavailable",
    );
    expect(screen.queryByLabelText("Workspace totals")).not.toBeInTheDocument();
  });
});

describe("candidate form", () => {
  it("creates an unapproved candidate without automatically checking a reference answer", async () => {
    const create = vi
      .spyOn(api, "createCase")
      .mockImplementation(async (value) => ({
        case: { ...value, id: "case-1" },
        status: "candidate",
        reviewer: null,
        reason: null,
      }));
    const approve = vi.spyOn(api, "approve");
    const onSaved = vi.fn();
    renderWithClient(<CaseEditor onSaved={onSaved} onClose={vi.fn()} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Case title"), "A deliberate case");
    await user.type(screen.getByLabelText("User message"), "A question");
    await user.type(
      screen.getByLabelText(/Reference answer/),
      "An authored reference",
    );
    await user.click(screen.getByRole("button", { name: "Save candidate" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
    expect(create.mock.calls[0][0].checks).toEqual([]);
    expect(create.mock.calls[0][0].turns[0].reference_answer).toBe(
      "An authored reference",
    );
    expect(approve).not.toHaveBeenCalled();
  });
  it("surfaces server validation errors without closing the editor", async () => {
    vi.spyOn(api, "createCase").mockRejectedValue(
      new Error("422: Invalid fixture version"),
    );
    const onSaved = vi.fn();
    renderWithClient(<CaseEditor onSaved={onSaved} onClose={vi.fn()} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Case title"), "Case");
    await user.type(screen.getByLabelText("User message"), "Question");
    await user.click(screen.getByRole("button", { name: "Save candidate" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Invalid fixture version",
    );
    expect(onSaved).not.toHaveBeenCalled();
  });
  it("does not discard practical fields when JSON conversion validation fails", async () => {
    renderWithClient(<CaseEditor onSaved={vi.fn()} onClose={vi.fn()} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Case title"), "Case");
    await user.type(screen.getByLabelText("User message"), "Question");
    await user.type(screen.getByLabelText(/Required tool/), "lookup");
    await user.type(screen.getByLabelText(/Argument path/), "id");
    await user.type(screen.getByLabelText(/Argument equals/), "unquoted");
    await user.click(
      screen.getByRole("button", { name: "Continue in canonical JSON" }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("valid JSON");
    expect(screen.getByLabelText(/Required tool/)).toHaveValue("lookup");
    expect(
      screen.queryByLabelText("Canonical case JSON"),
    ).not.toBeInTheDocument();
  });
});

describe("review and execution boundaries", () => {
  const value = {
    ...blankCanonical,
    id: "case-one",
    title: "Reviewable case",
    turns: [{ user: "Question", reference_answer: null }],
    checks: [
      {
        kind: "content_contains" as const,
        required: true,
        turn: 0,
        config: { value: "expected" },
      },
    ],
  };
  it("requires confirmation and a reason before approving the displayed revision", async () => {
    const record = {
      case: { ...value, revision: 3 },
      status: "candidate" as const,
      reviewer: null,
      reason: null,
    };
    vi.spyOn(api, "cases").mockResolvedValue([record]);
    vi.spyOn(api, "case").mockResolvedValue(record);
    const approve = vi
      .spyOn(api, "approve")
      .mockResolvedValue({ ...record, status: "approved" });
    renderWithClient(<Candidates />, "/candidates?case=case-one");
    const button = await screen.findByRole("button", {
      name: "Approve revision 3",
    });
    expect(button).toBeDisabled();
    const user = userEvent.setup();
    await user.type(
      screen.getByLabelText("Approval reason"),
      "Checked source and expectations",
    );
    expect(button).toBeDisabled();
    await user.click(
      screen.getByLabelText(
        "I reviewed the inputs, references, and check expectations.",
      ),
    );
    await user.click(button);
    await waitFor(() =>
      expect(approve).toHaveBeenCalledWith(
        "case-one",
        3,
        "Checked source and expectations",
      ),
    );
  });
  it("only exposes approved cases for release selection", async () => {
    vi.spyOn(api, "releases").mockResolvedValue([]);
    vi.spyOn(api, "cases").mockResolvedValue([
      { case: value, status: "approved", reviewer: null, reason: null },
      {
        case: { ...value, id: "candidate-two", title: "Not yet reviewed" },
        status: "candidate",
        reviewer: null,
        reason: null,
      },
    ]);
    renderWithClient(<Releases />);
    await userEvent.click(
      screen.getByRole("button", { name: "Create release" }),
    );
    expect(await screen.findByLabelText(/Reviewable case/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Not yet reviewed/)).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Publish 0 selected cases" }),
    ).toBeDisabled();
  });
  it("pins selected revisions and blocks background replacements until explicitly reselected", async () => {
    vi.spyOn(api, "releases").mockResolvedValue([]);
    vi.spyOn(api, "cases").mockResolvedValue([
      {
        case: { ...value, revision: 3 },
        status: "approved",
        reviewer: null,
        reason: null,
      },
    ]);
    const create = vi
      .spyOn(api, "createRelease")
      .mockRejectedValue(new ApiError(503, "Unavailable"));
    const { client } = renderWithClient(<Releases />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Create release" }));
    await user.type(screen.getByLabelText("Release name"), "Baseline");
    await user.click(await screen.findByLabelText(/Reviewable case/));
    await user.click(
      screen.getByLabelText(
        "Publish an immutable release of these approved revisions.",
      ),
    );
    await user.click(
      screen.getByRole("button", { name: "Publish 1 selected cases" }),
    );
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith("Baseline", ["case-one"], {
        "case-one": 3,
      }),
    );
    await screen.findByRole("alert");
    act(() => {
      client.setQueryData(api.key("cases"), [
        { case: { ...value, revision: 4 }, status: "approved" },
      ]);
    });
    await waitFor(() =>
      expect(screen.getByLabelText(/Reviewable case/)).not.toBeChecked(),
    );
    expect(
      screen.getByRole("button", { name: "Publish 1 selected cases" }),
    ).toBeDisabled();
    expect(
      screen.getByText(/No newer revision has been selected automatically/),
    ).toBeVisible();
    await user.click(screen.getByLabelText(/Reviewable case/));
    expect(
      screen.getByLabelText(
        "Publish an immutable release of these approved revisions.",
      ),
    ).not.toBeChecked();
    await user.click(
      screen.getByLabelText(
        "Publish an immutable release of these approved revisions.",
      ),
    );
    await user.click(
      screen.getByRole("button", { name: "Publish 1 selected cases" }),
    );
    await waitFor(() =>
      expect(create).toHaveBeenLastCalledWith("Baseline", ["case-one"], {
        "case-one": 4,
      }),
    );
  });
  it("refetches on release conflict without automatically selecting or retrying newer revisions", async () => {
    vi.spyOn(api, "releases").mockResolvedValue([]);
    const cases = vi
      .spyOn(api, "cases")
      .mockResolvedValueOnce([
        {
          case: { ...value, revision: 3 },
          status: "approved",
          reviewer: null,
          reason: null,
        },
      ])
      .mockResolvedValue([
        {
          case: { ...value, revision: 4 },
          status: "approved",
          reviewer: null,
          reason: null,
        },
      ]);
    const create = vi
      .spyOn(api, "createRelease")
      .mockRejectedValue(new ApiError(409, "Stale case revision"));
    renderWithClient(<Releases />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Create release" }));
    await user.type(screen.getByLabelText("Release name"), "Baseline");
    await user.click(
      await screen.findByLabelText("Select all eligible cases (1)"),
    );
    await user.click(
      screen.getByLabelText(
        "Publish an immutable release of these approved revisions.",
      ),
    );
    await user.click(
      screen.getByRole("button", { name: "Publish 1 selected cases" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Stale case revision",
    );
    await waitFor(() => expect(cases).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(/Revision 4/)).toBeVisible();
    expect(screen.getByLabelText(/Reviewable case/)).not.toBeChecked();
    expect(
      screen.getByLabelText(
        "Publish an immutable release of these approved revisions.",
      ),
    ).not.toBeChecked();
    expect(
      screen.getByRole("button", { name: "Publish 0 selected cases" }),
    ).toBeDisabled();
    expect(create).toHaveBeenCalledExactlyOnceWith("Baseline", ["case-one"], {
      "case-one": 3,
    });
    expect(screen.getByText(/Publication conflict/)).toBeVisible();
  });
  it.each(["failed", "interrupted"])(
    "blocks %s chat sessions and directs recovery to a new session",
    async (status) => {
      const session = {
        ...execution,
        id: "session-one",
        title: "Failed investigation",
        agent_revision: "fixed" as const,
        agent_revision_id: "synthetic-fixed",
        legacy: true,
        spec_hash: null,
        mode: "mock" as const,
        created_at: "2026-09-08",
        status,
        error: "Chat execution failed or timed out",
        trace_complete: false,
        messages: [],
        tool_calls: [],
      };
      vi.spyOn(api, "sessions").mockResolvedValue([session]);
      mockRegistry();
      vi.spyOn(api, "session").mockResolvedValue(session);
      const send = vi.spyOn(api, "sendMessage");
      renderWithClient(<Playground />, "/playground?session=session-one");
      expect(
        await screen.findByText(/Chat execution failed or timed out/),
      ).toBeVisible();
      expect(screen.getByLabelText("Your next turn")).toBeDisabled();
      expect(screen.getByText("incomplete")).toBeVisible();
      expect(screen.getByText("Unavailable for this historical execution")).toBeVisible();
      expect(screen.getByText(/Legacy compatibility record; original evidence retains/)).toHaveTextContent("fixed");
      expect(
        screen.getByRole("button", { name: "Send message" }),
      ).toBeDisabled();
      fireEvent.change(screen.getByLabelText("Your next turn"), {
        target: { value: "Cannot retry here" },
      });
      fireEvent.submit(
        screen.getByLabelText("Your next turn").closest("form")!,
      );
      expect(send).not.toHaveBeenCalled();
      const discard = vi.spyOn(window, "confirm").mockReturnValue(true);
      await userEvent.click(
        screen.getByRole("button", { name: "Start a new session" }),
      );
      expect(
        screen.getByRole("heading", { name: "Start an exploration" }),
      ).toBeVisible();
      expect(
        screen.getByRole("button", { name: "Create session" }),
      ).toBeDisabled();
      expect(screen.queryByLabelText("Your next turn")).not.toBeInTheDocument();
      expect(discard).toHaveBeenCalledOnce();
    },
  );
});
