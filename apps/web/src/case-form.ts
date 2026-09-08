import type { CanonicalCase, Check, CheckKind } from "./api";

export interface SimpleCaseFields {
  title: string;
  user: string;
  reference: string;
  content: string;
  contentKind: "content_contains" | "content_excludes" | "exact_match";
  tool: string;
  argumentPath: string;
  argumentValue: string;
  tags: string;
  context: string;
}
export const emptyFields: SimpleCaseFields = {
  title: "",
  user: "",
  reference: "",
  content: "",
  contentKind: "content_contains",
  tool: "",
  argumentPath: "",
  argumentValue: "",
  tags: "",
  context: "",
};
export const checkKinds: CheckKind[] = [
  "content_contains",
  "content_excludes",
  "exact_match",
  "tool_required",
  "tool_forbidden",
  "tool_arguments",
  "tool_order",
  "json_schema",
  "judge",
];

export function buildCase(fields: SimpleCaseFields): CanonicalCase {
  if (!fields.title.trim() || !fields.user.trim())
    throw new Error("A title and user message are required.");
  if (fields.argumentPath.trim() && !fields.tool.trim())
    throw new Error("An argument check requires a tool name.");
  const checks: Check[] = [];
  if (fields.content.trim())
    checks.push({
      kind: fields.contentKind,
      required: true,
      turn: 0,
      config: { value: fields.content },
    });
  if (fields.tool.trim())
    checks.push({
      kind: "tool_required",
      required: true,
      turn: 0,
      config: { tool: fields.tool.trim(), min: 1 },
    });
  if (fields.argumentPath.trim()) {
    let value: unknown;
    try {
      value = JSON.parse(fields.argumentValue);
    } catch {
      throw new Error(
        "Argument value must be valid JSON. Wrap a string in double quotes.",
      );
    }
    checks.push({
      kind: "tool_arguments",
      required: true,
      turn: 0,
      config: {
        tool: fields.tool.trim(),
        path: fields.argumentPath.trim(),
        operator: "equals",
        value,
      },
    });
  }
  return {
    revision: 1,
    title: fields.title.trim(),
    tags: fields.tags
      .split(",")
      .map((tag) => tag.trim())
      .filter(Boolean),
    source: { type: "manual", synthetic: true },
    context: fields.context,
    turns: [
      {
        user: fields.user.trim(),
        ...(fields.reference.trim()
          ? { reference_answer: fields.reference }
          : {}),
      },
    ],
    checks,
    fixture_version: "synthetic-v1",
  };
}

export function parseCase(text: string): CanonicalCase {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error("Canonical case must be valid JSON.");
  }
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Canonical case must be a JSON object.");
  const data = value as Record<string, unknown>;
  if (typeof data.title !== "string" || !data.title.trim())
    throw new Error("Case title is required.");
  if (!Number.isInteger(data.revision) || Number(data.revision) < 1)
    throw new Error("Revision must be a positive integer.");
  if (
    !Array.isArray(data.turns) ||
    !data.turns.length ||
    data.turns.some(
      (turn) =>
        !turn ||
        typeof turn.user !== "string" ||
        !turn.user.trim() ||
        (turn.reference_answer != null &&
          typeof turn.reference_answer !== "string"),
    )
  )
    throw new Error(
      "Provide at least one turn with a nonempty user message and an optional string or null reference answer.",
    );
  if (
    !Array.isArray(data.tags) ||
    data.tags.some((tag) => typeof tag !== "string")
  )
    throw new Error("Tags must be an array of strings.");
  if (
    !data.source ||
    typeof data.source !== "object" ||
    Array.isArray(data.source)
  )
    throw new Error("Source must be a JSON object.");
  if (
    typeof data.context !== "string" ||
    typeof data.fixture_version !== "string" ||
    !data.fixture_version
  )
    throw new Error(
      "Context and fixture_version must be strings; fixture_version cannot be empty.",
    );
  if (!Array.isArray(data.checks)) throw new Error("Checks must be an array.");
  for (const check of data.checks) {
    if (!check || !checkKinds.includes(check.kind))
      throw new Error(`Unknown check kind: ${check?.kind ?? "(missing)"}.`);
    if (typeof check.required !== "boolean")
      throw new Error("Every check must specify required as true or false.");
    if (
      check.turn !== null &&
      (!Number.isInteger(check.turn) ||
        check.turn < 0 ||
        check.turn >= data.turns.length)
    )
      throw new Error(
        "Check turn must be null or a valid zero-based turn index.",
      );
    if (
      !check.config ||
      typeof check.config !== "object" ||
      Array.isArray(check.config)
    )
      throw new Error("Every check needs a config object.");
  }
  return data as unknown as CanonicalCase;
}

export const blankCanonical: CanonicalCase = {
  revision: 1,
  title: "",
  tags: [],
  source: { type: "manual", synthetic: true },
  context: "",
  turns: [{ user: "" }],
  checks: [],
  fixture_version: "synthetic-v1",
};
