import { afterEach, describe, expect, it, vi } from "vitest";
import { api, request } from "../api";

afterEach(() => vi.unstubAllGlobals());

describe("API boundary", () => {
  it.each([
    { mode: "mock" as const, judge: undefined, expected: "none" },
    { mode: "mock" as const, judge: "azure" as const, expected: "azure" },
    { mode: "live" as const, judge: "none" as const, expected: "none" },
  ])(
    "sends judge $expected independently of $mode agent mode",
    async ({ mode, judge, expected }) => {
      const fetcher = vi.fn().mockResolvedValue(new Response("{}"));
      vi.stubGlobal("fetch", fetcher);
      await api.startRun("release-one", "fixed", mode, "request-key", judge);
      expect(fetcher).toHaveBeenCalledWith(
        "/api/v1/evaluation-runs",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            release_id: "release-one",
            agent_revision: "fixed",
            mode,
            idempotency_key: "request-key",
            judge: expected,
          }),
        }),
      );
    },
  );
  it("sends exact selected revisions alongside release case IDs", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetcher);
    await api.createRelease("Baseline", ["case-a", "case-b"], {
      "case-a": 3,
      "case-b": 7,
    });
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/dataset-releases",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          name: "Baseline",
          case_ids: ["case-a", "case-b"],
          expected_revisions: { "case-a": 3, "case-b": 7 },
        }),
      }),
    );
  });
  it("preserves the /api/v1 prefix and serializes explicit approval", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ status: "approved" }), { status: 200 }),
      );
    vi.stubGlobal("fetch", fetcher);
    await api.approve("case/id", 3, "Reviewed expectations");
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/cases/case%2Fid/approve",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ revision: 3, reason: "Reviewed expectations" }),
      }),
    );
  });
  it("lets the browser set multipart boundaries", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetcher);
    await api.preview(new File(["title,user"], "cases.csv"), "Scenarios");
    const options = fetcher.mock.calls[0][1];
    expect(options.headers).toEqual({});
    expect(options.body).toBeInstanceOf(FormData);
    expect(options.body.get("sheet")).toBe("Scenarios");
  });
  it("surfaces structured validation errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Revision conflict" }), {
          status: 409,
        }),
      ),
    );
    await expect(request("/cases/example")).rejects.toThrow(
      "409: Revision conflict",
    );
  });
  it("surfaces connection failures without returning fake data", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );
    await expect(api.summary()).rejects.toThrow("Cannot reach the API");
  });
});
