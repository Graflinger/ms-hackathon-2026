import type { Check, CheckKind, CanonicalCase } from "./api";

export const checkLabels: Record<CheckKind, string> = {
  content_contains: "Content contains",
  content_excludes: "Content excludes",
  exact_match: "Content exactly matches",
  tool_required: "Tool required",
  tool_forbidden: "Tool forbidden",
  tool_arguments: "Tool argument equals",
  tool_order: "Tool order (JSON)",
  json_schema: "Answer JSON schema (JSON)",
  judge: "Judge rubric",
};

export function newCheck(kind: CheckKind): Check {
  const configs: Record<CheckKind, Check["config"]> = {
    content_contains: { value: "" },
    content_excludes: { value: "" },
    exact_match: { value: "" },
    tool_required: { tool: "", min: 1 },
    tool_forbidden: { tool: "" },
    tool_arguments: { tool: "", path: "", operator: "equals", value: "" },
    tool_order: { tools: [] },
    json_schema: { schema: {} },
    judge: { rubric: "", threshold: 0.8 },
  };
  return { kind, required: true, turn: 0, config: configs[kind] };
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown) => typeof value === "string" && !!value.trim();

export function parseConfig(value: string): Check["config"] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error("Check configuration must be valid JSON.");
  }
  if (!isObject(parsed)) throw new Error("Check configuration must be a JSON object.");
  return parsed;
}

// This is authoring feedback, not a replacement for SDK/server validation.
// Unknown configuration keys are deliberately retained for the server to validate.
export function checkError(check: Check, turnCount: number): string | null {
  if (check.turn !== null && (!Number.isInteger(check.turn) || check.turn < 0 || check.turn >= turnCount))
    return "Choose an existing turn or All turns.";
  const c = check.config;
  if (!isObject(c)) return "Check configuration must be a JSON object.";
  switch (check.kind) {
    case "content_contains":
    case "content_excludes":
    case "exact_match":
      return text(c.value) ? null : "Expected content is required.";
    case "tool_required":
    case "tool_forbidden": {
      if (!text(c.tool)) return "Tool name is required.";
      if (c.alternatives !== undefined && (!Array.isArray(c.alternatives) || c.alternatives.some((v) => !text(v))))
        return "Tool alternatives must be an array of nonempty names.";
      const min = c.min === undefined ? 1 : c.min;
      const max = c.max;
      if (!Number.isInteger(min) || Number(min) < 0 || (max != null && (!Number.isInteger(max) || Number(max) < 0)))
        return "Tool occurrence bounds must be nonnegative integers.";
      if (check.kind === "tool_required" && (Number(min) < 1 || (max != null && Number(max) < Number(min))))
        return "Required tool minimum must be positive and maximum at least minimum.";
      if (check.kind === "tool_forbidden" && (min !== 1 || (max != null && max !== 0)))
        return "Forbidden tools cannot have nonzero occurrence bounds.";
      return null;
    }
    case "tool_arguments":
      if (!text(c.tool)) return "Tool name is required.";
      if (typeof c.path !== "string") return "Argument path must be a string (empty selects all arguments).";
      if (!["equals", "subset", "type", "schema", "range"].includes(String(c.operator)))
        return "Choose a supported argument operator in JSON.";
      if (!Object.hasOwn(c, "value")) return "Argument value is required as JSON.";
      return null;
    case "judge":
      if (!text(c.rubric)) return "Judge rubric is required.";
      return typeof c.threshold === "number" && Number.isFinite(c.threshold) && c.threshold >= 0 && c.threshold <= 1
        ? null : "Judge threshold must be a number between 0 and 1.";
    case "tool_order":
      return Array.isArray(c.tools) && c.tools.length > 0 && c.tools.every(text)
        ? null : "Tool order needs a nonempty array of tool names.";
    case "json_schema":
      return isObject(c.schema) ? null : "Answer schema must be a JSON object.";
    default:
      // Future kinds can be inspected and preserved; validity remains server-owned.
      return null;
  }
}

export function hasRequiredActionableCheck(value: CanonicalCase): boolean {
  return value.checks.some((check) =>
    check.required && Object.hasOwn(checkLabels, check.kind) && !checkError(check, value.turns.length),
  );
}

// Fall back to JSON when a form would hide configuration or coerce its types.
export function supportsStructured(check: Check): boolean {
  const c = check.config;
  const keys: Partial<Record<CheckKind, string[]>> = {
    content_contains: ["value"], content_excludes: ["value"], exact_match: ["value"],
    tool_required: ["tool", "min"], tool_forbidden: ["tool"],
    tool_arguments: ["tool", "path", "operator", "value"], judge: ["rubric", "threshold"],
  };
  const allowed = keys[check.kind as CheckKind];
  if (!allowed || !isObject(c) || Object.keys(c).some((key) => !allowed.includes(key))) return false;
  if (check.kind.startsWith("content_") || check.kind === "exact_match") return typeof c.value === "string";
  if (check.kind === "judge") return typeof c.rubric === "string" && typeof c.threshold === "number";
  if (typeof c.tool !== "string") return false;
  if (check.kind === "tool_arguments") return c.operator === "equals" && typeof c.path === "string" && Object.hasOwn(c, "value");
  return c.min === undefined || typeof c.min === "number";
}

export interface CheckDraft {
  key: number;
  check: Check;
  advanced: boolean;
  config: string;
  argument: string;
  threshold: string;
  minimum: string;
}

export function checkDraft(check: Check, key: number): CheckDraft {
  return {
    key, check, advanced: !supportsStructured(check),
    config: JSON.stringify(check.config, null, 2),
    argument: JSON.stringify(check.config.value) ?? "",
    threshold: String(check.config.threshold ?? ""),
    minimum: String(check.config.min ?? 1),
  };
}

export function buildCheck(draft: CheckDraft, turnCount: number): Check {
  const config = draft.advanced ? parseConfig(draft.config) : { ...draft.check.config };
  if (!draft.advanced) {
    if (draft.check.kind === "tool_arguments") {
      try {
        config.value = JSON.parse(draft.argument);
      } catch {
        throw new Error('Argument value must be valid JSON. Wrap strings in double quotes.');
      }
    }
    if (draft.check.kind === "judge") config.threshold = draft.threshold.trim() ? Number(draft.threshold) : NaN;
    if (draft.check.kind === "tool_required" && (Object.hasOwn(config, "min") || draft.minimum !== "1"))
      config.min = draft.minimum.trim() ? Number(draft.minimum) : NaN;
  }
  const check = { ...draft.check, config };
  const error = checkError(check, turnCount);
  if (error) throw new Error(error);
  return check;
}
