import { afterEach, describe, expect, it, vi } from "vitest";
import { createProjectApi, request } from "../api";
const api = createProjectApi("synthetic-demo");

afterEach(() => vi.unstubAllGlobals());

describe("API boundary", () => {
  it("blocks new writes with unknown metadata but permits history, cancellation and export", async () => {
    let writable = false;
    const scoped = createProjectApi("synthetic-demo", () => writable);
    const fetcher = vi.fn().mockImplementation(async () => new Response("{}"));
    vi.stubGlobal("fetch", fetcher);
    await expect(scoped.createAgent({ name: "Blocked" })).rejects.toThrow("Project writes are disabled");
    await expect(scoped.createSession("Blocked", "synthetic-fixed")).rejects.toThrow("Project writes are disabled");
    expect(fetcher).not.toHaveBeenCalled();
    await scoped.cases();
    await scoped.cancelRun("run-one");
    await scoped.exportBundle("release-one", "synthetic-fixed", "mock", "none");
    expect(fetcher).toHaveBeenCalledTimes(3);
    writable = true;
    await scoped.createAgent({ name: "Recovered" });
    expect(fetcher).toHaveBeenCalledTimes(4);
  });
  it("downloads configured v2 bundles as binary without implicit execution defaults", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response("zip-content", {
          headers: { "Content-Type": "application/zip" },
        }),
      );
    vi.stubGlobal("fetch", fetcher);
    const blob = await api.exportBundle(
      "release/one",
      "synthetic-fixed",
      "mock",
      "none",
    );
    expect(await blob.text()).toBe("zip-content");
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v2/projects/synthetic-demo/dataset-releases/release%2Fone/export?agent_revision_id=synthetic-fixed&mode=mock&judge=none",
      expect.any(Object),
    );
  });
  it.each([
    { mode: "mock" as const, judge: undefined, expected: "none" },
    { mode: "mock" as const, judge: "azure" as const, expected: "azure" },
    { mode: "live" as const, judge: "none" as const, expected: "none" },
  ])(
    "sends judge $expected independently of $mode agent mode",
    async ({ mode, judge, expected }) => {
      const fetcher = vi.fn().mockResolvedValue(new Response("{}"));
      vi.stubGlobal("fetch", fetcher);
      await api.startRun(
        "release-one",
        "synthetic-fixed",
        mode,
        "request-key",
        judge,
      );
      expect(fetcher).toHaveBeenCalledWith(
        "/api/v2/projects/synthetic-demo/evaluation-runs",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            release_id: "release-one",
            agent_revision_id: "synthetic-fixed",
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
      "/api/v2/projects/synthetic-demo/dataset-releases",
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
  it("scopes the v2 prefix and serializes explicit approval", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ status: "approved" }), { status: 200 }),
      );
    vi.stubGlobal("fetch", fetcher);
    await api.approve("case/id", 3, "Reviewed expectations");
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v2/projects/synthetic-demo/cases/case%2Fid/approve",
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
