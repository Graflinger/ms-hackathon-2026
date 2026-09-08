import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { api, ApiError, type RunDetail } from "../api";
import Runs from "../pages/Runs";

const release = {
  id: "release-one",
  name: "Baseline",
  created_at: "2026-09-08",
  content_hash: "hash",
  case_count: 1,
};
const run: RunDetail = {
  id: "run-one",
  release_id: release.id,
  agent_revision: "fixed",
  mode: "mock",
  status: "completed",
  gate: "pass",
  created_at: "2026-09-08",
  results: [],
  release_name: null,
  error: null,
  lineage: {},
};
const judgeConsent =
  "I authorize Azure judge model usage costs, independently of agent execution costs.";
const agentConsent =
  "I intend to invoke the configured live agent, which may incur model usage costs.";

function renderRuns(route = "/runs?release=release-one") {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  vi.spyOn(api, "runs").mockResolvedValue([]);
  vi.spyOn(api, "releases").mockResolvedValue([release]);
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <Runs />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("judge launch controls", () => {
  it("requires Azure consent for a mock agent and resets consent when changing judge", async () => {
    const launch = vi
      .spyOn(api, "startRun")
      .mockRejectedValue(
        new ApiError(409, "Azure judge configuration missing"),
      );
    renderRuns();
    const user = userEvent.setup();
    await screen.findByLabelText("Golden release");
    expect(screen.getByLabelText("Judge provider")).toHaveValue("none");
    await user.selectOptions(screen.getByLabelText("Judge provider"), "azure");
    const button = screen.getByRole("button", { name: "Start evaluation" });
    expect(button).toBeDisabled();
    expect(screen.queryByLabelText(agentConsent)).not.toBeInTheDocument();
    expect(
      screen.getByText("GOLDENLOOP_ALLOW_LIVE_SYNTHETIC_JUDGE=true"),
    ).toBeVisible();
    expect(screen.getByText("GOLDENLOOP_JUDGE_API_KEY")).toBeVisible();
    fireEvent.submit(button.closest("form")!);
    expect(launch).not.toHaveBeenCalled();
    await user.click(screen.getByLabelText(judgeConsent));
    await user.click(button);
    await waitFor(() =>
      expect(launch).toHaveBeenCalledWith(
        release.id,
        "fixed",
        "mock",
        expect.any(String),
        "azure",
      ),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Azure judge configuration missing",
    );
    await user.selectOptions(screen.getByLabelText("Judge provider"), "none");
    await user.selectOptions(screen.getByLabelText("Judge provider"), "azure");
    expect(screen.getByLabelText(judgeConsent)).not.toBeChecked();
    expect(button).toBeDisabled();
  });

  it("requires separate opt-ins when both agent and judge are live", async () => {
    const launch = vi
      .spyOn(api, "startRun")
      .mockRejectedValue(new ApiError(409, "Live configuration missing"));
    renderRuns();
    const user = userEvent.setup();
    await screen.findByLabelText("Golden release");
    await user.selectOptions(screen.getByLabelText("Execution mode"), "live");
    await user.selectOptions(screen.getByLabelText("Judge provider"), "azure");
    const button = screen.getByRole("button", { name: "Start evaluation" });
    await user.click(screen.getByLabelText(agentConsent));
    expect(button).toBeDisabled();
    await user.click(screen.getByLabelText(agentConsent));
    await user.click(screen.getByLabelText(judgeConsent));
    expect(button).toBeDisabled();
    fireEvent.submit(button.closest("form")!);
    expect(launch).not.toHaveBeenCalled();
    await user.click(screen.getByLabelText(agentConsent));
    expect(button).toBeEnabled();
    await user.click(button);
    await waitFor(() =>
      expect(launch).toHaveBeenCalledWith(
        release.id,
        "fixed",
        "live",
        expect.any(String),
        "azure",
      ),
    );
  });

  it("surfaces required-judge 409 errors and includes judge in retry identity", async () => {
    const launch = vi
      .spyOn(api, "startRun")
      .mockRejectedValue(
        new ApiError(409, "Required judge checks need a configured provider"),
      );
    renderRuns();
    const user = userEvent.setup();
    await screen.findByLabelText("Golden release");
    expect(
      screen.getByText(/required judge checks are rejected with HTTP 409/),
    ).toBeVisible();
    const button = screen.getByRole("button", { name: "Start evaluation" });
    await user.click(button);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Required judge checks need a configured provider",
    );
    const firstKey = launch.mock.calls[0][3];
    expect(launch.mock.calls[0][4]).toBe("none");
    await user.click(button);
    await waitFor(() => expect(launch).toHaveBeenCalledTimes(2));
    expect(launch.mock.calls[1][3]).toBe(firstKey);
    await user.selectOptions(screen.getByLabelText("Judge provider"), "azure");
    await user.click(screen.getByLabelText(judgeConsent));
    await user.click(button);
    await waitFor(() => expect(launch).toHaveBeenCalledTimes(3));
    expect(launch.mock.calls[2][3]).not.toBe(firstKey);
    expect(launch.mock.calls[2][4]).toBe("azure");
    expect(
      screen.queryByText("Evaluation in progress.", { exact: false }),
    ).not.toBeInTheDocument();
  });
});

describe("per-case observation telemetry", () => {
  it.each([
    {
      observation: {
        latency_ms: 125.5,
        usage: { input_tokens: 42, output_tokens: 0 },
      },
      latency: "125.5 ms",
      reported: true,
    },
    {
      observation: {
        latency_ms: 0,
        usage: { input_tokens: 42, output_tokens: 0 },
      },
      latency: "0 ms",
      reported: true,
    },
    {
      observation: { latency_ms: null, usage: {} },
      latency: "Not reported",
      reported: false,
    },
    { observation: null, latency: "Not reported", reported: false },
  ])(
    "renders reported telemetry without invented totals: $latency",
    async ({ observation, latency, reported }) => {
      vi.spyOn(api, "run").mockResolvedValue({
        ...run,
        results: [
          {
            case_id: "case-one",
            case_revision: 2,
            gate: "pass",
            checks: [],
            agent_revision: "fixed",
            mode: "mock",
            // Deliberately malformed wire data exercises defensive rendering.
            observation: observation as RunDetail["results"][number]["observation"],
          },
        ],
      });
      renderRuns("/runs?run=run-one");
      await userEvent.click(await screen.findByText("Case case-one"));
      const telemetry = within(
        screen.getByRole("region", { name: "Case observation telemetry" }),
      );
      expect(
        telemetry.getByText("Latency").nextElementSibling,
      ).toHaveTextContent(latency);
      if (reported) {
        expect(
          telemetry.getByText("input_tokens").nextElementSibling,
        ).toHaveTextContent("42");
        expect(
          telemetry.getByText("output_tokens").nextElementSibling,
        ).toHaveTextContent("0");
      } else {
        expect(telemetry.getAllByText("Not reported")).toHaveLength(2);
        expect(telemetry.queryByText("0")).not.toBeInTheDocument();
      }
      expect(
        telemetry.getByText(
          /No aggregate usage, judge usage, or cost is inferred/,
        ),
      ).toBeVisible();
      expect(screen.getByText("Not reported at run level")).toBeVisible();
    },
  );
});
