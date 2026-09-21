import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type CaseRecord, type Check, type Feedback, type Session } from "../api";
import { blankCanonical } from "../case-form";
import { buildCheck, checkDraft, hasRequiredActionableCheck } from "../check-form";
import { ChecksEditor } from "../checks-editor";
import Candidates from "../pages/Candidates";
import Playground from "../pages/Playground";
import { api, execution, mockRegistry, project, TestProject } from "./project-fixtures";
import { ProjectProvider } from "../project";

const original: CaseRecord = {
  case: {
    ...blankCanonical, id: "feedback-case", revision: 3, title: "Review VaR incidents",
    fixture_version: "synthetic-powerplant-v1", tags: ["split:validation", "incident"],
    context: "Synthetic investigation",
    source: { type: "chat", synthetic: true, session_id: "session-var", feedback_id: "feedback-one", evidence: { call: "observed-1" } },
    turns: [{ user: "List incidents", reference_answer: null }, { user: "Assess the turbine", reference_answer: "Reviewer reference" }],
  },
  status: "candidate", reviewer: null, reason: "From feedback",
};

function mount(ui: React.ReactNode, route = "/candidates?case=feedback-case") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  const view = render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[route]}><TestProject>{ui}</TestProject></MemoryRouter></QueryClientProvider>);
  return { ...view, client };
}

async function addCheck(user: ReturnType<typeof userEvent.setup>, kind: string, index: number) {
  await user.selectOptions(screen.getByLabelText("New check kind"), kind);
  await user.click(screen.getByRole("button", { name: "Add check" }));
  return within(screen.getByRole("group", { name: new RegExp(`^Check ${index}:`) }));
}
const change = (element: HTMLElement, value: string) => fireEvent.change(element, { target: { value } });

describe("checks authoring from review", () => {
  it("takes multi-turn Playground feedback through explicit VaR checks, revision save and approval", async () => {
    let current = structuredClone(original);
    const session: Session = {
      ...execution, id: "session-var", title: "VaR investigation", agent_revision: execution.agent_revision_id,
      mode: "mock", created_at: project.created_at, status: "completed", error: null, trace_complete: true,
      messages: current.case.turns.flatMap((turn, index) => [
        { id: `u${index}`, command_id: `c${index}`, role: "user", content: turn.user, turn: index },
        { id: `a${index}`, command_id: `c${index}`, role: "assistant", content: "Observed answer, not an expectation", turn: index },
      ]), tool_calls: [],
    };
    const feedback: Feedback = {
      id: "feedback-one", session_id: session.id, target: "answer", turn: 1, tool_call_id: "",
      issue_type: "missing_tool", comment: "Review analysis tools", correction: null, status: "unresolved", reviewer: "", reason: null,
      candidate_id: null, created_at: project.created_at,
    };
    mockRegistry();
    vi.spyOn(api, "sessions").mockResolvedValue([session]);
    vi.spyOn(api, "session").mockResolvedValue(session);
    const addFeedback = vi.spyOn(api, "addFeedback").mockResolvedValue(feedback);
    vi.spyOn(api, "feedback").mockResolvedValue([feedback]);
    const create = vi.spyOn(api, "feedbackCandidate").mockResolvedValue(current);
    vi.spyOn(api, "cases").mockImplementation(async () => [current]);
    vi.spyOn(api, "case").mockImplementation(async () => current);
    const update = vi.spyOn(api, "updateCase").mockImplementation(async (_id, value) => {
      current = { ...current, case: { ...value, revision: 4 } };
      return current;
    });
    const approve = vi.spyOn(api, "approve").mockImplementation(async () => {
      current = { ...current, status: "approved" };
      return current;
    });
    mount(<Routes>
      <Route path="/projects/:project/playground" element={<Playground />} />
      <Route path="/projects/:project/candidates" element={<Candidates />} />
    </Routes>, `/projects/${project.id}/playground?session=session-var`);
    const user = userEvent.setup();
    await screen.findByLabelText("Your next turn");
    await user.click(screen.getAllByRole("button", { name: "Give answer feedback" })[1]);
    change(screen.getByLabelText("Issue type"), "missing_tool");
    change(screen.getByLabelText("Reviewer comment"), feedback.comment);
    await user.click(screen.getByRole("button", { name: "Submit feedback" }));
    await user.click(await screen.findByRole("link", { name: "Review feedback and create a candidate" }));
    expect(addFeedback).toHaveBeenCalledWith(expect.objectContaining({ session_id: session.id, turn: 1 }));
    await user.click(await screen.findByText("missing tool"));
    await user.click(screen.getByRole("button", { name: "Create unreviewed candidate" }));
    await waitFor(() => expect(create).toHaveBeenCalledWith(feedback.id));
    await user.click(screen.getByRole("button", { name: "Candidates & approvals" }));
    await user.click(await screen.findByRole("button", { name: `Review ${current.case.title}` }));
    expect(await screen.findByRole("button", { name: "Approve revision 3" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Add checks" }));
    expect(screen.queryByLabelText("Tool name")).not.toBeInTheDocument();
    for (const [index, name] of ["similar_occurrences", "risk_assessment", "commercial_outlook"].entries()) {
      const group = await addCheck(user, "tool_required", index + 1);
      expect(group.getByLabelText("Required")).toBeChecked();
      expect(group.getByLabelText(/^Tool name/)).toHaveValue("");
      change(group.getByLabelText(/^Tool name/), name);
      await user.selectOptions(group.getByLabelText("Applies to"), "1");
    }
    const argument = await addCheck(user, "tool_arguments", 4);
    change(argument.getByLabelText(/^Tool name/), "risk_assessment");
    change(argument.getByLabelText(/^Argument path/), "category");
    change(argument.getByLabelText(/^Argument equals/), '"turbine"');
    await user.selectOptions(argument.getByLabelText("Applies to"), "all");
    change(screen.getByLabelText("Revision reason"), "Authored tool requirements after review");
    await user.click(screen.getByRole("button", { name: "Save new candidate revision" }));
    await waitFor(() => expect(update).toHaveBeenCalledOnce());
    const submitted = update.mock.calls[0][1];
    expect(submitted).toEqual({ ...original.case, checks: [
      ...["similar_occurrences", "risk_assessment", "commercial_outlook"].map((tool) => ({ kind: "tool_required", required: true, turn: 1, config: { tool, min: 1 } })),
      { kind: "tool_arguments", required: true, turn: null, config: { tool: "risk_assessment", path: "category", operator: "equals", value: "turbine" } },
    ] });
    expect(update).toHaveBeenCalledWith(original.case.id, submitted, 3, "Authored tool requirements after review");
    expect(approve).not.toHaveBeenCalled();
    const button = await screen.findByRole("button", { name: "Approve revision 4" });
    change(screen.getByLabelText("Approval reason"), "Validated the authored expectations");
    await user.click(screen.getByLabelText("I reviewed the inputs, references, and check expectations."));
    await user.click(button);
    await waitFor(() => expect(approve).toHaveBeenCalledWith(original.case.id, 4, "Validated the authored expectations"));
  });

  it.each(["chat", "import", "manual"])("offers checks authoring for %s candidates", async (origin) => {
    const record = { ...original, case: { ...original.case, source: { type: origin } } };
    vi.spyOn(api, "cases").mockResolvedValue([record]);
    vi.spyOn(api, "case").mockResolvedValue(record);
    mount(<Candidates />);
    await userEvent.click(await screen.findByRole("button", { name: "Add checks" }));
    expect(screen.getByRole("heading", { name: "Edit checks / revision 3" })).toBeVisible();
  });

  it.each([
    { checks: [] },
    { checks: [{ kind: "content_contains", required: false, turn: 0, config: { value: "expected" } }] },
    { checks: [{ kind: "tool_required", required: true, turn: 0, config: { tool: "" } }] },
  ] as { checks: Check[] }[])("blocks approval without required actionable checks ($checks)", async ({ checks }) => {
    const record = { ...original, case: { ...original.case, checks } };
    vi.spyOn(api, "cases").mockResolvedValue([record]);
    vi.spyOn(api, "case").mockResolvedValue(record);
    const approve = vi.spyOn(api, "approve");
    mount(<Candidates />);
    const button = await screen.findByRole("button", { name: "Approve revision 3" });
    change(screen.getByLabelText("Approval reason"), "Reviewed");
    await userEvent.click(screen.getByLabelText("I reviewed the inputs, references, and check expectations."));
    expect(button).toBeDisabled();
    expect(screen.getByText(/Approval requires at least one required actionable check/)).toBeVisible();
    fireEvent.submit(button.closest("form")!);
    await screen.findByRole("alert");
    expect(approve).not.toHaveBeenCalled();
  });

  it("preserves existing IDs, unknown configs, complex arguments and untouched case fields while editing", async () => {
    const checks: Check[] = [
      { id: "tool-id", kind: "tool_required", required: true, turn: 1, config: { tool: "risk_assessment", min: 2, max: 5, alternatives: ["fallback"], future: { nested: [false, null] } } },
      { id: "future-id", kind: "future_check", required: false, turn: null, config: { arbitrary: { a: [1, null] } } },
      { id: "arg-id", kind: "tool_arguments", required: true, turn: 0, config: { tool: "risk_assessment", path: "", operator: "equals", value: { category: ["turbine", null], enabled: false } } },
      { id: "content-id", kind: "content_excludes", required: true, turn: 0, config: { value: "unvalidated" } },
    ];
    const record = { ...original, case: { ...original.case, checks } };
    const update = vi.spyOn(api, "updateCase").mockResolvedValue(record);
    mount(<ChecksEditor record={record} onClose={vi.fn()} onSaved={vi.fn()} />);
    expect(screen.getAllByLabelText("Check configuration JSON")).toHaveLength(2);
    expect(JSON.parse((screen.getAllByLabelText("Check configuration JSON")[0] as HTMLTextAreaElement).value)).toEqual(checks[0].config);
    const user = userEvent.setup();
    await user.click(screen.getAllByRole("button", { name: "Use structured fields" })[0]);
    expect(screen.getByRole("alert")).toHaveTextContent("requires JSON to preserve all settings");
    change(screen.getByLabelText("Expected content"), "Unsupported conclusion");
    change(screen.getByLabelText("Revision reason"), "Refine exclusion");
    await user.click(screen.getByRole("button", { name: "Save new candidate revision" }));
    await waitFor(() => expect(update).toHaveBeenCalledOnce());
    expect(update.mock.calls[0][1]).toEqual({ ...record.case, checks: [...checks.slice(0, 3), { ...checks[3], config: { value: "Unsupported conclusion" } }] });
  });

  it("retains edits on a revision conflict and prevents stale resubmission", async () => {
    const update = vi.spyOn(api, "updateCase").mockRejectedValue(new ApiError(409, "Stale case revision"));
    const saved = vi.fn();
    mount(<ChecksEditor record={original} onClose={vi.fn()} onSaved={saved} />);
    change(screen.getByLabelText("Revision reason"), "My review");
    await userEvent.click(screen.getByRole("button", { name: "Save new candidate revision" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Stale case revision");
    expect(screen.getByText(/Revision conflict. Your draft is retained/)).toBeVisible();
    expect(screen.getByLabelText("Revision reason")).toHaveValue("My review");
    const button = screen.getByRole("button", { name: "Save new candidate revision" });
    expect(button).toBeDisabled();
    fireEvent.submit(button.closest("form")!);
    expect(update).toHaveBeenCalledOnce();
    expect(saved).not.toHaveBeenCalled();
  });

  it("saves optional judge, exact content, forbidden tool and advanced order edits", async () => {
    const update = vi.spyOn(api, "updateCase").mockResolvedValue(original);
    mount(<ChecksEditor record={original} onClose={vi.fn()} onSaved={vi.fn()} />);
    const user = userEvent.setup();
    const judge = await addCheck(user, "judge", 1);
    change(judge.getByLabelText("Judge rubric"), "Explain uncertainty using the available evidence");
    change(judge.getByLabelText("Judge threshold"), "0.75");
    await user.click(judge.getByLabelText("Required"));
    const exact = await addCheck(user, "exact_match", 2);
    change(exact.getByLabelText("Expected content"), "Reviewer-authored text");
    const forbidden = await addCheck(user, "tool_forbidden", 3);
    change(forbidden.getByLabelText(/^Tool name/), "write_incident");
    const order = await addCheck(user, "tool_order", 4);
    change(order.getByLabelText("Check configuration JSON"), '{"tools":["similar_occurrences","risk_assessment"]}');
    change(screen.getByLabelText("Revision reason"), "Authored mixed expectations");
    await user.click(screen.getByRole("button", { name: "Save new candidate revision" }));
    await waitFor(() => expect(update).toHaveBeenCalledOnce());
    expect(update.mock.calls[0][1].checks).toEqual([
      { kind: "judge", required: false, turn: 0, config: { rubric: "Explain uncertainty using the available evidence", threshold: 0.75 } },
      { kind: "exact_match", required: true, turn: 0, config: { value: "Reviewer-authored text" } },
      { kind: "tool_forbidden", required: true, turn: 0, config: { tool: "write_incident" } },
      { kind: "tool_order", required: true, turn: 0, config: { tools: ["similar_occurrences", "risk_assessment"] } },
    ]);
  });

  it("requires valid fields and a reason, supports removal and protects cancelled drafts", async () => {
    const update = vi.spyOn(api, "updateCase");
    const close = vi.fn();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    mount(<ChecksEditor record={original} onClose={close} onSaved={vi.fn()} />);
    const user = userEvent.setup();
    const button = screen.getByRole("button", { name: "Save new candidate revision" });
    await user.click(button);
    expect(screen.getByRole("alert")).toHaveTextContent("revision reason is required");
    change(screen.getByLabelText("Revision reason"), "Reviewed");
    await addCheck(user, "content_contains", 1);
    await user.click(button);
    expect(screen.getByRole("alert")).toHaveTextContent("Expected content is required");
    await user.click(screen.getByRole("button", { name: "Remove check 1" }));
    const tool = await addCheck(user, "tool_arguments", 1);
    await user.click(button);
    expect(screen.getByRole("alert")).toHaveTextContent("Tool name is required");
    change(tool.getByLabelText(/^Tool name/), "risk_assessment");
    change(tool.getByLabelText(/^Argument equals/), "unquoted");
    await user.click(button);
    expect(screen.getByRole("alert")).toHaveTextContent("valid JSON");
    await user.click(screen.getByRole("button", { name: "Remove check 1" }));
    const judge = await addCheck(user, "judge", 1);
    change(judge.getByLabelText("Judge rubric"), "Grounded analysis");
    change(judge.getByLabelText("Judge threshold"), "1.1");
    await user.click(button);
    expect(screen.getByRole("alert")).toHaveTextContent("between 0 and 1");
    await user.click(screen.getByRole("button", { name: "Remove check 1" }));
    const schema = await addCheck(user, "json_schema", 1);
    change(schema.getByLabelText("Check configuration JSON"), "[]");
    await user.click(button);
    expect(screen.getByRole("alert")).toHaveTextContent("JSON object");
    expect(update).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(confirm).toHaveBeenCalledOnce();
    expect(close).not.toHaveBeenCalled();
    expect(schema.getByLabelText("Check configuration JSON")).toHaveValue("[]");
    confirm.mockReturnValue(true);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(close).toHaveBeenCalledOnce();
  });

  it("blocks saving if the project is archived after editing starts", async () => {
    const update = vi.spyOn(api, "updateCase");
    const client = new QueryClient();
    const view = (archived: boolean) => <QueryClientProvider client={client}><ProjectProvider project={{ ...project, archived }} api={api}><ChecksEditor record={original} onClose={vi.fn()} onSaved={vi.fn()} /></ProjectProvider></QueryClientProvider>;
    const { rerender } = render(view(false));
    change(screen.getByLabelText("Revision reason"), "Draft before archive");
    rerender(view(true));
    const button = screen.getByRole("button", { name: "Save new candidate revision" });
    expect(button).toBeDisabled();
    fireEvent.submit(button.closest("form")!);
    expect(update).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Revision reason")).toHaveValue("Draft before archive");
  });

  it("does not invoke an old editor callback after unmounting during save", async () => {
    let resolve!: (value: CaseRecord) => void;
    vi.spyOn(api, "updateCase").mockReturnValue(new Promise((done) => { resolve = done; }));
    const saved = vi.fn();
    const { unmount } = mount(<ChecksEditor record={original} onClose={vi.fn()} onSaved={saved} />);
    change(screen.getByLabelText("Revision reason"), "Reviewed");
    await userEvent.click(screen.getByRole("button", { name: "Save new candidate revision" }));
    unmount();
    await act(async () => { resolve(original); });
    expect(saved).not.toHaveBeenCalled();
  });
});

describe("check configuration fidelity", () => {
  it.each([
    { kind: "exact_match", config: { value: "Exact text" } },
    { kind: "tool_forbidden", config: { tool: "write_tool" } },
    { kind: "tool_order", config: { tools: ["first", "second"] } },
    { kind: "json_schema", config: { schema: { type: "object", required: ["category"] } } },
    { kind: "judge", config: { rubric: "Accurate category", threshold: 0 } },
    { kind: "tool_arguments", config: { tool: "risk_assessment", path: "category", operator: "subset", value: { category: "turbine" } } },
  ])("roundtrips $kind without materializing or losing config fields", ({ kind, config }) => {
    const check: Check = { id: "kept", kind, config, required: false, turn: null };
    expect(buildCheck(checkDraft(check, 0), 2)).toEqual(check);
  });
  it("maps required actionable checks without accepting unknown kinds or out-of-range turns", () => {
    expect(hasRequiredActionableCheck({ ...original.case, checks: [{ kind: "future", required: true, turn: 0, config: {} }] })).toBe(false);
    expect(() => buildCheck(checkDraft({ kind: "exact_match", required: true, turn: 2, config: { value: "answer" } }, 0), 2)).toThrow("existing turn");
  });
});
