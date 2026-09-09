import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { createProjectApi, type Project } from "../api";
import { blankCanonical } from "../case-form";
import { agent, execution, project, revision } from "./project-fixtures";
import { defaultSpec, parseAgentSpec } from "../pages/Agents";

afterEach(() => vi.unstubAllGlobals());
const second: Project = {
  ...project,
  id: "second-project",
  name: "Second project",
};
const release = {
  id: "release-one",
  name: "Reviewed baseline",
  created_at: project.created_at,
  content_hash: "b".repeat(64),
  case_count: 1,
};
const caseRecord = {
  case: {
    ...blankCanonical,
    id: "case-one",
    title: "Demo case",
    turns: [{ user: "Find customer 42", reference_answer: null }],
  },
  status: "candidate",
  reviewer: null,
  reason: null,
};
type Handler = (path: string, init?: RequestInit) => unknown | Promise<unknown>;

function mount(
  route = `/projects/${project.id}/candidates`,
  overrides?: Handler,
  archived = false,
) {
  const projects = [{ ...project, archived }, second];
  const fetcher = vi.fn(async (input: string, init?: RequestInit) => {
    const result = await overrides?.(input, init);
    if (result instanceof Response) return result;
    if (result !== undefined) return new Response(JSON.stringify(result));
    const pathname = input.split("?")[0];
    if (pathname === "/api/v1/health")
      return new Response(
        JSON.stringify({ status: "ok", mode: "mock", sdk_version: "0.2.0" }),
      );
    if (pathname === "/api/v2/projects")
      return new Response(JSON.stringify(projects));
    const match = pathname.match(/^\/api\/v2\/projects\/([^/]+)(.*)$/);
    if (!match) throw new Error(`Unexpected route ${input}`);
    const [, id, resource] = match;
    let value: unknown;
    if (!resource) value = projects.find((p) => p.id === id);
    else if (resource === "/summary")
      value = {
        candidates: id === project.id ? 1 : 0,
        releases: 0,
        runs: 0,
        feedback: 0,
      };
    else if (resource === "/cases")
      value = id === project.id ? [caseRecord] : [];
    else if (resource === "/cases/case-one") value = caseRecord;
    else if (resource === "/agents") value = id === project.id ? [agent] : [];
    else if (resource.endsWith("/revisions")) value = [revision];
    else if (resource === "/dataset-releases") value = [release];
    else if (resource === `/dataset-releases/${release.id}`)
      value = { ...release, cases: [caseRecord.case] };
    else value = [];
    return new Response(JSON.stringify(value));
  });
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity, gcTime: Infinity },
      mutations: { retry: false },
    },
  });
  const router = createMemoryRouter([{ path: "*", element: <App /> }], {
    initialEntries: [route],
  });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { client, router, fetcher };
}

async function chooseRevision() {
  const user = userEvent.setup();
  await screen.findByRole("option", { name: agent.name });
  await user.selectOptions(screen.getByLabelText("Agent"), agent.id);
  await screen.findByRole("option", { name: "Fixed / r2" });
  await user.selectOptions(
    screen.getByLabelText("Agent revision"),
    revision.id,
  );
}

describe("project navigation and lifecycle", () => {
  it("guards dirty revision query selections and Back without treating hash or query ordering as resource changes", async () => {
    const other = { ...agent, id: "other-agent", name: "Other agent" };
    const { router } = mount(
      `/projects/${project.id}/agents?agent=${other.id}`,
      (path) => (path.endsWith("/agents") ? [agent, other] : undefined),
    );
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    await screen.findByRole("option", { name: agent.name });
    await act(async () => {
      await router.navigate(
        `/projects/${project.id}/agents?agent=${agent.id}&view=history`,
      );
    });
    await user.click(
      await screen.findByRole("button", { name: "New revision" }),
    );
    await user.type(
      screen.getByLabelText("Revision label"),
      "Unsaved revision",
    );
    await act(async () => {
      await router.navigate(
        `/projects/${project.id}/agents?view=history&agent=${agent.id}#main`,
      );
    });
    expect(confirm).not.toHaveBeenCalled();
    await user.selectOptions(screen.getByLabelText("Inspect agent"), other.id);
    await waitFor(() => expect(confirm).toHaveBeenCalledOnce());
    expect(screen.getByLabelText("Inspect agent")).toHaveValue(agent.id);
    expect(screen.getByLabelText("Revision label")).toHaveValue(
      "Unsaved revision",
    );
    // Back over the hash is safe; Back over the agent selection must be blocked.
    await act(async () => {
      await router.navigate(-1);
    });
    expect(confirm).toHaveBeenCalledOnce();
    await act(async () => {
      await router.navigate(-1);
    });
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(2));
    expect(screen.getByLabelText("Revision label")).toHaveValue(
      "Unsaved revision",
    );
    confirm.mockReturnValue(true);
    await act(async () => {
      await router.navigate(-1);
    });
    await waitFor(() =>
      expect(screen.getByLabelText("Inspect agent")).toHaveValue(other.id),
    );
    expect(screen.queryByLabelText("Revision label")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Inspect agent"), agent.id);
    await user.click(screen.getByRole("button", { name: "New revision" }));
    expect(screen.getByLabelText("Revision label")).toHaveValue("");
  });

  it.each(["message", "feedback"])(
    "retains an unsent chat %s when session query navigation is declined",
    async (draft) => {
      const session = {
        ...execution,
        id: "session-one",
        title: "First conversation",
        agent_revision: revision.id,
        created_at: project.created_at,
        status: "completed",
        error: null,
        trace_complete: true,
        messages: [{ role: "assistant", content: "Observed answer", turn: 0 }],
        tool_calls: [],
      };
      const other = {
        ...session,
        id: "session-two",
        title: "Second conversation",
      };
      const { router } = mount(
        `/projects/${project.id}/playground?session=${session.id}`,
        (path) => {
          if (path.endsWith("/chat-sessions")) return [session, other];
          if (path.endsWith(`/chat-sessions/${session.id}`)) return session;
          if (path.endsWith(`/chat-sessions/${other.id}`)) return other;
        },
      );
      const user = userEvent.setup();
      const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
      await screen.findByLabelText("Your next turn");
      if (draft === "message")
        await user.type(
          screen.getByLabelText("Your next turn"),
          "Unsent question",
        );
      else {
        await user.click(
          screen.getByRole("button", { name: "Give answer feedback" }),
        );
        await user.type(
          screen.getByLabelText("Reviewer comment"),
          "Unsent review signal",
        );
      }
      await user.selectOptions(screen.getByLabelText("Conversation"), other.id);
      await waitFor(() => expect(confirm).toHaveBeenCalledOnce());
      expect(router.state.location.search).toBe(`?session=${session.id}`);
      expect(
        screen.getByLabelText(
          draft === "message" ? "Your next turn" : "Reviewer comment",
        ),
      ).toHaveValue(
        draft === "message" ? "Unsent question" : "Unsent review signal",
      );
      confirm.mockReturnValue(true);
      await user.selectOptions(screen.getByLabelText("Conversation"), other.id);
      await screen.findByRole("heading", { name: other.title });
      expect(screen.getByLabelText("Your next turn")).toHaveValue("");
      expect(
        screen.queryByLabelText("Reviewer comment"),
      ).not.toBeInTheDocument();
    },
  );

  it("guards candidate approval drafts on query-only selection, retaining confirmation after a declined Back", async () => {
    const other = {
      ...caseRecord,
      case: { ...caseRecord.case, id: "case-two", title: "Other case" },
    };
    const { router } = mount(
      `/projects/${project.id}/candidates?tab=cases&case=${other.case.id}`,
      (path) => {
        if (path.endsWith("/cases")) return [caseRecord, other];
        if (path.endsWith(`/cases/${other.case.id}`)) return other;
      },
    );
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    await user.click(
      await screen.findByRole("button", { name: "Review Demo case" }),
    );
    await user.type(
      await screen.findByLabelText("Approval reason"),
      "Reviewed expectations",
    );
    await user.click(
      screen.getByLabelText(
        "I reviewed the inputs, references, and check expectations.",
      ),
    );
    await act(async () => {
      await router.navigate(-1);
    });
    await waitFor(() => expect(confirm).toHaveBeenCalledOnce());
    expect(screen.getByLabelText("Approval reason")).toHaveValue(
      "Reviewed expectations",
    );
    expect(
      screen.getByLabelText(
        "I reviewed the inputs, references, and check expectations.",
      ),
    ).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Review Other case" }));
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(2));
    confirm.mockReturnValue(true);
    await user.click(screen.getByRole("button", { name: "Review Other case" }));
    await waitFor(() =>
      expect(router.state.location.search).toBe("?case=case-two"),
    );
    expect(await screen.findByLabelText("Approval reason")).toHaveValue("");
    expect(
      screen.getByLabelText(
        "I reviewed the inputs, references, and check expectations.",
      ),
    ).not.toBeChecked();
  });

  it.each(["candidate", "revision"])(
    "retains a dirty %s through metadata refetch failure and retry, blocking writes until recovery",
    async (editor) => {
      let fail = false;
      const { client, fetcher } = mount(
        `/projects/${project.id}/${editor === "candidate" ? "candidates" : `agents?agent=${agent.id}`}`,
        (path) => {
          if (path === `/api/v2/projects/${project.id}` && fail)
            return new Response(
              JSON.stringify({ detail: "Temporary metadata outage" }),
              { status: 503 },
            );
        },
      );
      const user = userEvent.setup();
      const label = editor === "candidate" ? "Case title" : "Revision label";
      const saveLabel =
        editor === "candidate" ? "Save candidate" : "Create revision";
      await user.click(
        await screen.findByRole("button", {
          name: editor === "candidate" ? "New candidate" : "New revision",
        }),
      );
      await user.type(screen.getByLabelText(label), "Retained draft");
      if (editor === "candidate")
        await user.type(
          screen.getByLabelText("User message"),
          "Synthetic input",
        );
      const originalInput = screen.getByLabelText(label);
      fail = true;
      await act(async () => {
        await client.invalidateQueries({
          queryKey: ["project", project.id, "metadata"],
        });
      });
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Temporary metadata outage",
      );
      expect(screen.getByLabelText(label)).toBe(originalInput);
      expect(originalInput).toHaveValue("Retained draft");
      expect(screen.getByRole("button", { name: saveLabel })).toBeDisabled();
      fireEvent.submit(originalInput.closest("form")!);
      expect(
        fetcher.mock.calls.some(([, init]) => init?.method === "POST"),
      ).toBe(false);
      await user.click(screen.getByRole("button", { name: "Try again" }));
      await waitFor(() =>
        expect(
          client.getQueryState(["project", project.id, "metadata"])
            ?.fetchStatus,
        ).toBe("idle"),
      );
      expect(screen.getByLabelText(label)).toBe(originalInput);
      fail = false;
      await user.click(screen.getByRole("button", { name: "Try again" }));
      await waitFor(() =>
        expect(screen.queryByRole("alert")).not.toBeInTheDocument(),
      );
      expect(screen.getByLabelText(label)).toBe(originalInput);
      expect(originalInput).toHaveValue("Retained draft");
      expect(screen.getByRole("button", { name: saveLabel })).toBeEnabled();
      const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
      await user.selectOptions(
        screen.getByLabelText("Current project"),
        second.id,
      );
      await waitFor(() => expect(confirm).toHaveBeenCalledOnce());
      expect(screen.getByLabelText(label)).toBe(originalInput);
    },
  );

  it("late candidate saves invalidate their project without closing or navigating a replacement editor", async () => {
    let resolve!: (value: unknown) => void;
    const pending = new Promise((r) => {
      resolve = r;
    });
    const { client, router } = mount(undefined, (path, init) =>
      path.endsWith("/cases") && init?.method === "POST" ? pending : undefined,
    );
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "New candidate" }),
    );
    await user.type(screen.getByLabelText("Case title"), "Submitting A");
    await user.type(screen.getByLabelText("User message"), "Synthetic A");
    await user.click(screen.getByRole("button", { name: "Save candidate" }));
    await screen.findByRole("button", { name: "Saving..." });
    await user.click(screen.getByRole("button", { name: "Close case editor" }));
    await user.click(screen.getByRole("button", { name: "New candidate" }));
    await user.type(screen.getByLabelText("Case title"), "Replacement B");
    await act(async () => resolve(caseRecord));
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: ["project", project.id, "cases"],
      }),
    );
    expect(screen.getByLabelText("Case title")).toHaveValue("Replacement B");
    expect(router.state.location.search).toBe("");
    expect(confirm).not.toHaveBeenCalled();
    await user.selectOptions(
      screen.getByLabelText("Current project"),
      second.id,
    );
    await waitFor(() => expect(confirm).toHaveBeenCalledOnce());
    expect(screen.getByLabelText("Case title")).toHaveValue("Replacement B");
  });

  it("late revision saves cannot close a replacement revision editor", async () => {
    let resolve!: (value: unknown) => void;
    const pending = new Promise((r) => {
      resolve = r;
    });
    const { client } = mount(
      `/projects/${project.id}/agents?agent=${agent.id}`,
      (path, init) =>
        path.endsWith("/revisions") && init?.method === "POST"
          ? pending
          : undefined,
    );
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "New revision" }),
    );
    await user.type(
      screen.getByLabelText("Revision label"),
      "Submitting revision A",
    );
    await user.click(screen.getByRole("button", { name: "Create revision" }));
    await screen.findByRole("button", { name: "Creating..." });
    await user.click(screen.getByText("Fixed / r2"));
    await user.click(
      screen.getByRole("button", { name: "Create from this revision" }),
    );
    await user.type(
      screen.getByLabelText("Revision label"),
      "Replacement revision B",
    );
    await act(async () => resolve(revision));
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: ["project", project.id, "revisions", agent.id],
      }),
    );
    expect(screen.getByLabelText("Revision label")).toHaveValue(
      "Replacement revision B",
    );
  });

  it("successful candidate saves clear only their initiating draft before query navigation", async () => {
    const { router } = mount(undefined, (path, init) =>
      path.endsWith("/cases") && init?.method === "POST"
        ? caseRecord
        : undefined,
    );
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "New candidate" }),
    );
    await user.type(screen.getByLabelText("Case title"), "Saved candidate");
    await user.type(screen.getByLabelText("User message"), "Synthetic input");
    await user.click(screen.getByRole("button", { name: "Save candidate" }));
    await waitFor(() =>
      expect(router.state.location.search).toBe("?case=case-one"),
    );
    expect(confirm).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Case title")).not.toBeInTheDocument();
  });

  it("guards direct navigation with dirty editors, including browser history transitions", async () => {
    const { router } = mount();
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    await screen.findByRole("button", { name: "Review Demo case" });
    await user.click(screen.getByRole("button", { name: "New candidate" }));
    await user.type(screen.getByLabelText("Case title"), "Keep this draft");
    await act(async () => {
      await router.navigate(`/projects/${second.id}`);
    });
    await waitFor(() => expect(confirm).toHaveBeenCalledOnce());
    expect(router.state.location.pathname).toBe(
      `/projects/${project.id}/candidates`,
    );
    expect(screen.getByLabelText("Case title")).toHaveValue("Keep this draft");
    confirm.mockReturnValue(true);
    await act(async () => {
      await router.navigate(`/projects/${second.id}`);
    });
    await screen.findByRole("heading", { name: "Make every answer count." });
    expect(screen.queryByLabelText("Case title")).not.toBeInTheDocument();
  });

  it("does not display a late project query response after switching", async () => {
    let resolve!: (value: unknown) => void;
    const pending = new Promise((r) => {
      resolve = r;
    });
    const { client } = mount(undefined, (path) =>
      path === `/api/v2/projects/${project.id}/cases` ? pending : undefined,
    );
    const user = userEvent.setup();
    await screen.findByRole("option", { name: second.name });
    await user.selectOptions(
      screen.getByLabelText("Current project"),
      second.id,
    );
    await screen.findByRole("heading", { name: "Make every answer count." });
    await act(async () => resolve([caseRecord]));
    await waitFor(() =>
      expect(client.getQueryData(["project", second.id, "cases"])).toEqual([]),
    );
    expect(screen.queryByText("Demo case")).not.toBeInTheDocument();
  });
  it("redirects legacy links explicitly to Synthetic Demo and preserves search", async () => {
    localStorage.setItem("goldenloop.project", second.id);
    const { router } = mount("/candidates?tab=feedback");
    await screen.findByRole("heading", {
      name: "Signals from real interactions",
    });
    expect(router.state.location.pathname).toBe(
      `/projects/${project.id}/candidates`,
    );
    expect(router.state.location.search).toBe("?tab=feedback");
  });

  it("shows projects and creates the first new workspace through v2", async () => {
    const { fetcher } = mount("/", (path, init) =>
      path === "/api/v2/projects" && init?.method === "POST"
        ? second
        : undefined,
    );
    const user = userEvent.setup();
    await screen.findByRole("heading", { name: "Synthetic Demo" });
    await user.click(screen.getByRole("button", { name: "New project" }));
    await user.type(screen.getByLabelText("Project name"), "New workspace");
    await user.type(
      screen.getByLabelText("Description"),
      "Synthetic experiment",
    );
    await user.click(screen.getByRole("button", { name: "Create project" }));
    await waitFor(() =>
      expect(fetcher).toHaveBeenCalledWith(
        "/api/v2/projects",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            name: "New workspace",
            description: "Synthetic experiment",
          }),
        }),
      ),
    );
  });

  it("confirms dirty switching, isolates cached cases, and resets editors on return", async () => {
    const { client } = mount();
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    await screen.findByRole("button", { name: "Review Demo case" });
    await user.click(screen.getByRole("button", { name: "New candidate" }));
    await user.type(screen.getByLabelText("Case title"), "Unsaved expectation");
    await user.selectOptions(
      screen.getByLabelText("Current project"),
      second.id,
    );
    expect(confirm).toHaveBeenCalledOnce();
    expect(screen.getByLabelText("Case title")).toHaveValue(
      "Unsaved expectation",
    );
    confirm.mockReturnValue(true);
    await user.selectOptions(
      screen.getByLabelText("Current project"),
      second.id,
    );
    await screen.findByRole("heading", { name: "Make every answer count." });
    expect(screen.queryByText("Demo case")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Case title")).not.toBeInTheDocument();
    expect(client.getQueryData(["project", project.id, "cases"])).toEqual([
      caseRecord,
    ]);
    expect(client.getQueryData(["project", second.id, "cases"])).toEqual([]);
    await user.selectOptions(
      screen.getByLabelText("Current project"),
      project.id,
    );
    await screen.findByText("Demo case");
    await user.click(
      within(
        screen.getByRole("complementary", { name: "Main navigation" }),
      ).getByRole("link", { name: /Candidates/ }),
    );
    await user.click(screen.getByRole("button", { name: "New candidate" }));
    expect(screen.getByLabelText("Case title")).toHaveValue("");
    expect(localStorage.getItem("goldenloop.project")).toBe(project.id);
  });

  it("keeps an in-flight mutation's invalidations and navigation in the captured project", async () => {
    let resolve!: (value: unknown) => void;
    const pending = new Promise((r) => {
      resolve = r;
    });
    const { client, router, fetcher } = mount(undefined, (path, init) =>
      path.endsWith("/cases") && init?.method === "POST" ? pending : undefined,
    );
    const invalidate = vi.spyOn(client, "invalidateQueries");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    await screen.findByRole("button", { name: "Review Demo case" });
    await user.click(screen.getByRole("button", { name: "New candidate" }));
    await user.type(screen.getByLabelText("Case title"), "Pending candidate");
    await user.type(screen.getByLabelText("User message"), "Synthetic input");
    await user.click(screen.getByRole("button", { name: "Save candidate" }));
    await waitFor(() =>
      expect(
        fetcher.mock.calls.some(
          ([path, init]) => path.endsWith("/cases") && init?.method === "POST",
        ),
      ).toBe(true),
    );
    await user.selectOptions(
      screen.getByLabelText("Current project"),
      second.id,
    );
    await screen.findByRole("heading", { name: "Make every answer count." });
    await act(async () => resolve(caseRecord));
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: ["project", project.id, "cases"],
      }),
    );
    expect(
      invalidate.mock.calls.every(
        ([filters]) => filters?.queryKey?.[1] === project.id,
      ),
    ).toBe(true);
    expect(router.state.location.pathname).toBe(`/projects/${second.id}`);
    expect(router.state.location.search).toBe("");
  });

  it("resets agent selection and provider cost confirmations when switching projects", async () => {
    mount(`/projects/${project.id}/runs?release=${release.id}`);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    await screen.findByLabelText("Golden release");
    await chooseRevision();
    await user.selectOptions(screen.getByLabelText("Execution mode"), "live");
    await user.click(screen.getByLabelText(/I intend to invoke/));
    await user.selectOptions(screen.getByLabelText("Judge provider"), "azure");
    await user.click(screen.getByLabelText(/I authorize Azure judge/));
    await user.selectOptions(
      screen.getByLabelText("Current project"),
      second.id,
    );
    await screen.findByRole("heading", { name: "Make every answer count." });
    await user.selectOptions(
      screen.getByLabelText("Current project"),
      project.id,
    );
    await screen.findByText("Demo case");
    await user.click(
      within(
        screen.getByRole("complementary", { name: "Main navigation" }),
      ).getByRole("link", { name: /Evaluation runs/ }),
    );
    await user.click(screen.getByRole("button", { name: "New evaluation" }));
    expect(screen.getByLabelText("Agent revision")).toHaveValue("");
    expect(screen.getByLabelText("Execution mode")).toHaveValue("mock");
    expect(screen.getByLabelText("Judge provider")).toHaveValue("none");
    expect(
      screen.queryByLabelText(/I authorize Azure judge/),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Start evaluation" }),
    ).toBeDisabled();
  });

  it("blocks archived project writes while retaining run cancellation", async () => {
    const run = {
      ...execution,
      id: "run-one",
      release_id: release.id,
      agent_revision: revision.id,
      mode: "mock",
      status: "running",
      gate: null,
      created_at: project.created_at,
      release_name: release.name,
      results: [],
      lineage: {},
      error: null,
    };
    const { fetcher } = mount(
      `/projects/${project.id}/runs?run=run-one`,
      (path) =>
        path.endsWith("/evaluation-runs")
          ? [run]
          : path.includes("/evaluation-runs/run-one")
            ? run
            : undefined,
      true,
    );
    const user = userEvent.setup();
    await screen.findByRole("button", { name: "Cancel run" });
    expect(
      screen.getByRole("button", { name: "New evaluation" }),
    ).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Cancel run" }));
    await user.click(
      screen.getByRole("button", { name: "Confirm cancellation" }),
    );
    await waitFor(() =>
      expect(fetcher).toHaveBeenCalledWith(
        `/api/v2/projects/${project.id}/evaluation-runs/run-one/cancel`,
        expect.objectContaining({ method: "POST" }),
      ),
    );
    await user.click(screen.getByRole("link", { name: /Candidates/ }));
    expect(
      screen.getByRole("button", { name: "New candidate" }),
    ).toBeDisabled();
  });
});

describe("registry and explicit export", () => {
  it("edits project metadata and sends archive separately from display fields", async () => {
    const { fetcher } = mount(`/projects/${project.id}`, (path, init) => {
      if (path === `/api/v2/projects/${project.id}` && init?.method === "PATCH")
        return project;
    });
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await screen.findByText(`Project settings / ${project.name}`);
    await user.click(screen.getByText(`Project settings / ${project.name}`));
    await user.clear(screen.getByLabelText("Project name"));
    await user.type(screen.getByLabelText("Project name"), "Renamed project");
    await user.click(screen.getByRole("button", { name: "Save project" }));
    await waitFor(() =>
      expect(fetcher).toHaveBeenCalledWith(
        `/api/v2/projects/${project.id}`,
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({
            name: "Renamed project",
            description: project.description,
          }),
        }),
      ),
    );
    await user.click(screen.getByRole("button", { name: "Archive project" }));
    await waitFor(() =>
      expect(fetcher).toHaveBeenCalledWith(
        `/api/v2/projects/${project.id}`,
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ archived: true }),
        }),
      ),
    );
  });

  it("creates from an existing revision without mutating its original specification", async () => {
    const { fetcher } = mount(
      `/projects/${project.id}/agents?agent=${agent.id}`,
      (path, init) => {
        if (path.endsWith("/revisions") && init?.method === "POST")
          return revision;
      },
    );
    const user = userEvent.setup();
    await user.click(await screen.findByText("Fixed / r2"));
    await user.click(
      screen.getByRole("button", { name: "Create from this revision" }),
    );
    await user.type(screen.getByLabelText("Revision label"), "Mock successor");
    await user.click(
      screen.getByLabelText("Enable live mode in addition to mock"),
    );
    await user.selectOptions(
      screen.getByLabelText("Behavior variant"),
      "buggy",
    );
    await user.click(screen.getByRole("button", { name: "Create revision" }));
    await waitFor(() =>
      expect(fetcher).toHaveBeenCalledWith(
        `/api/v2/projects/${project.id}/agents/${agent.id}/revisions`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            label: "Mock successor",
            spec: {
              ...revision.spec,
              variant: "buggy",
              modes: ["mock"],
              connection: null,
            },
          }),
        }),
      ),
    );
    expect(revision.spec.variant).toBe("fixed");
    expect(revision.spec.modes).toEqual(["mock", "live"]);
  });

  it("excludes archived agents from new sessions and runs", async () => {
    mount(`/projects/${project.id}/runs?release=${release.id}`, (path) =>
      path.endsWith("/agents") ? [{ ...agent, archived: true }] : undefined,
    );
    await screen.findByText(/No active agents/);
    expect(
      screen.queryByRole("option", { name: agent.name }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Start evaluation" }),
    ).toBeDisabled();
  });
  it("creates an agent and an immutable mock revision with the approved contract", async () => {
    const { fetcher } = mount(
      `/projects/${project.id}/agents`,
      (path, init) => {
        if (init?.method !== "POST") return;
        if (path.endsWith("/agents")) return agent;
        if (path.endsWith("/revisions"))
          return {
            ...revision,
            label: "Reviewed implementation",
            spec: defaultSpec,
          };
      },
    );
    const user = userEvent.setup();
    await screen.findByRole("button", { name: "New agent" });
    await user.click(screen.getByRole("button", { name: "New agent" }));
    await user.type(screen.getByLabelText("Agent name"), "Support agent");
    await user.click(screen.getByRole("button", { name: "Create agent" }));
    await user.click(
      await screen.findByRole("button", { name: "New revision" }),
    );
    await user.type(
      screen.getByLabelText("Revision label"),
      "Reviewed implementation",
    );
    await user.click(screen.getByRole("button", { name: "Create revision" }));
    await waitFor(() =>
      expect(fetcher).toHaveBeenCalledWith(
        `/api/v2/projects/${project.id}/agents/${agent.id}/revisions`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            label: "Reviewed implementation",
            spec: defaultSpec,
          }),
        }),
      ),
    );
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(
      false,
    );
  });

  it("requires explicit revision and mode, permits archived export, and surfaces backend errors", async () => {
    const { fetcher } = mount(
      `/projects/${project.id}/releases?release=${release.id}`,
      (path) => {
        if (path.endsWith("/agents")) return [{ ...agent, archived: true }];
        if (path.includes("/export?"))
          return new Response(
            JSON.stringify({ detail: "Release incompatible with revision" }),
            { status: 409 },
          );
      },
      true,
    );
    const user = userEvent.setup();
    const button = await screen.findByRole("button", {
      name: "Export test bundle",
    });
    expect(button).toBeDisabled();
    expect(
      screen.queryByRole("link", { name: "Export test bundle" }),
    ).not.toBeInTheDocument();
    await screen.findByRole("option", { name: `${agent.name} / archived` });
    await user.selectOptions(screen.getByLabelText("Agent"), agent.id);
    await screen.findByRole("option", { name: "Fixed / r2" });
    await user.selectOptions(
      screen.getByLabelText("Agent revision"),
      revision.id,
    );
    expect(button).toBeDisabled();
    await user.selectOptions(
      screen.getByLabelText("Export execution mode"),
      "mock",
    );
    await user.click(button);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Release incompatible with revision",
    );
    expect(fetcher).toHaveBeenCalledWith(
      `/api/v2/projects/${project.id}/dataset-releases/${release.id}/export?agent_revision_id=${revision.id}&mode=mock&judge=none`,
      expect.any(Object),
    );
  });

  it("pins session creation to the selected stable revision ID", async () => {
    const { fetcher } = mount(
      `/projects/${project.id}/playground`,
      (path, init) =>
        path.endsWith("/chat-sessions") && init?.method === "POST"
          ? new Response(JSON.stringify({ detail: "Test boundary" }), {
              status: 409,
            })
          : undefined,
    );
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "New session" }),
    );
    expect(
      screen.getByRole("button", { name: "Create session" }),
    ).toBeDisabled();
    await chooseRevision();
    await user.click(screen.getByRole("button", { name: "Create session" }));
    await screen.findByRole("alert");
    expect(fetcher).toHaveBeenCalledWith(
      `/api/v2/projects/${project.id}/chat-sessions`,
      expect.objectContaining({
        body: JSON.stringify({ agent_revision_id: revision.id }),
        method: "POST",
      }),
    );
  });

  it("rejects secret fields and unapproved connection metadata before submission", () => {
    expect(parseAgentSpec(JSON.stringify(defaultSpec), [])).toEqual(
      defaultSpec,
    );
    expect(() =>
      parseAgentSpec(
        JSON.stringify({ ...defaultSpec, api_key: "plaintext" }),
        [],
      ),
    ).toThrow("Secret values");
    expect(() => parseAgentSpec(JSON.stringify(revision.spec), [])).toThrow(
      "server-approved binding",
    );
    expect(() =>
      parseAgentSpec(
        JSON.stringify({
          ...defaultSpec,
          connection: { ...revision.spec.connection, token: "plaintext" },
        }),
        [],
      ),
    ).toThrow("never a key");
  });

  it("captures independently scoped API factories, even with concurrent requests", async () => {
    const fetcher = vi.fn().mockImplementation(async () => new Response("[]"));
    vi.stubGlobal("fetch", fetcher);
    await createProjectApi("project/a").cases();
    await createProjectApi("project-b").cases();
    expect(fetcher.mock.calls.map(([path]) => path)).toEqual([
      "/api/v2/projects/project%2Fa/cases",
      "/api/v2/projects/project-b/cases",
    ]);
  });
});
