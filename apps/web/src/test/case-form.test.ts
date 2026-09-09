import { describe, expect, it } from "vitest";
import {
  blankCanonical,
  buildCase,
  emptyFields,
  parseCase,
} from "../case-form";
import { initialMapping } from "../pages/Import";
import { comparableRuns } from "../pages/Runs";
import { isActiveRun, type Run } from "../api";

describe("case authoring", () => {
  it("builds only explicitly authored expectations", () => {
    const result = buildCase({
      ...emptyFields,
      title: "  A reviewed scenario  ",
      user: "Question",
      reference: "Proposed answer",
      tags: "one, two, ",
    });
    expect(result.title).toBe("A reviewed scenario");
    expect(result.tags).toEqual(["one", "two"]);
    expect(result.turns).toEqual([
      { user: "Question", reference_answer: "Proposed answer" },
    ]);
    expect(result.checks).toEqual([]);
    expect(result.fixture_version).toBe("synthetic-v1");
  });
  it("uses zero-based turns and typed tool argument values", () => {
    const result = buildCase({
      ...emptyFields,
      title: "Check tools",
      user: "Question",
      content: "ready",
      tool: "lookup",
      argumentPath: "count",
      argumentValue: "2",
    });
    expect(result.checks).toEqual([
      {
        kind: "content_contains",
        required: true,
        turn: 0,
        config: { value: "ready" },
      },
      {
        kind: "tool_required",
        required: true,
        turn: 0,
        config: { tool: "lookup", min: 1 },
      },
      {
        kind: "tool_arguments",
        required: true,
        turn: 0,
        config: { tool: "lookup", path: "count", operator: "equals", value: 2 },
      },
    ]);
  });
  it("rejects incomplete authoring and invalid argument JSON", () => {
    expect(() => buildCase(emptyFields)).toThrow("title and user");
    expect(() =>
      buildCase({
        ...emptyFields,
        title: "Case",
        user: "Question",
        argumentPath: "id",
      }),
    ).toThrow("tool name");
    expect(() =>
      buildCase({
        ...emptyFields,
        title: "Case",
        user: "Question",
        tool: "lookup",
        argumentPath: "id",
        argumentValue: "unquoted",
      }),
    ).toThrow("valid JSON");
  });
  it("preserves advanced multi-turn and optional judge configuration", () => {
    const value = {
      ...blankCanonical,
      title: "Multiturn",
      turns: [{ user: "First" }, { user: "Second" }],
      checks: [
        {
          kind: "judge",
          required: false,
          turn: 1,
          config: { rubric: "Accuracy", threshold: 0.9 },
        },
      ],
    };
    expect(parseCase(JSON.stringify(value))).toEqual(value);
  });
  it("rejects malformed JSON and invalid turn references", () => {
    expect(() => parseCase("{")).toThrow("valid JSON");
    expect(() => parseCase("[]")).toThrow("JSON object");
    expect(() =>
      parseCase(
        JSON.stringify({
          ...blankCanonical,
          title: "Case",
          turns: [{ user: "Question" }],
          checks: [
            {
              kind: "content_contains",
              required: true,
              turn: 1,
              config: { value: "x" },
            },
          ],
        }),
      ),
    ).toThrow("zero-based");
  });
  it("accepts null references returned by the canonical SDK without rewriting them", () => {
    const value = {
      ...blankCanonical,
      title: "Imported case",
      turns: [{ user: "Question", reference_answer: null }],
    };
    expect(parseCase(JSON.stringify(value))).toEqual(value);
  });
});

describe("import mapping", () => {
  it("maps only canonical column names without inventing expectations", () => {
    expect(
      initialMapping(["Title", "user", "reference_answer", "Unrecognized"]),
    ).toEqual({
      title: "Title",
      user: "user",
      reference_answer: "reference_answer",
    });
    expect(initialMapping(["Question", "Answer"])).toEqual({});
  });
});

describe("run comparison", () => {
  const run: Run = {
    legacy: true,
    project_id: "synthetic-demo",
    agent_id: "synthetic-customer-lookup",
    agent_name: "Synthetic Customer Lookup",
    agent_revision_id: "synthetic-fixed",
    agent_revision_label: "Fixed",
    spec_hash: null,
    id: "a",
    release_id: "release-one",
    agent_revision: "fixed",
    mode: "mock",
    status: "completed",
    gate: "pass",
    release_name: null,
    created_at: "2026-09-08",
  };
  it("excludes other releases and the selected run", () => {
    const other: Run = { ...run, id: "b", agent_revision: "buggy", agent_revision_id: "synthetic-buggy", agent_revision_label: "Buggy" };
    expect(
      comparableRuns(
        [
          run,
          other,
          { ...run, id: "c", release_id: "release-two" },
          { ...run, id: "foreign", project_id: "other-project" },
        ],
        run,
      ),
    ).toEqual([other]);
  });
  it("polls active execution but not incomplete terminal execution", () => {
    expect(isActiveRun({ ...run, status: "running" })).toBe(true);
    expect(isActiveRun({ ...run, status: "incomplete" })).toBe(false);
    expect(isActiveRun({ ...run, status: "cancelled" })).toBe(false);
  });
});
