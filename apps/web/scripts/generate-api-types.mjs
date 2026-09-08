import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const output = resolve(root, "apps/web/src/generated/api-schema.d.ts");
const check = process.argv.includes("--check");
if (process.argv.slice(2).some((arg) => arg !== "--check")) {
  throw new Error("Usage: node apps/web/scripts/generate-api-types.mjs [--check]");
}
const temporary = mkdtempSync(join(tmpdir(), "goldenloop-openapi-"));

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: root,
    encoding: "utf8",
    maxBuffer: 16 * 1024 * 1024,
    ...options,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`${command} failed:\n${result.stdout}\n${result.stderr}`);
  }
  return result.stdout;
}

try {
  const schema = process.env.GOLDENLOOP_PYTHON
    ? run(process.env.GOLDENLOOP_PYTHON, ["-m", "goldenloop_api.openapi"])
    : run("uv", ["run", "--frozen", "--package", "goldenloop-api", "python", "-m", "goldenloop_api.openapi"]);
  JSON.parse(schema);
  const input = join(temporary, "openapi.json");
  const generated = join(temporary, "api-schema.d.ts");
  writeFileSync(input, schema);
  // npm exec isolates the pinned generator from the application's package files.
  const args = ["exec", "--yes", "--package=openapi-typescript@7.13.0", "--", "openapi-typescript", input, "--default-non-nullable", "false", "-o", generated];
  if (process.platform === "win32") {
    const npm = process.env.npm_execpath || join(dirname(process.execPath), "node_modules/npm/bin/npm-cli.js");
    run(process.execPath, [npm, ...args]);
  } else {
    run("npm", args);
  }
  const content = readFileSync(generated, "utf8").replaceAll("\r\n", "\n");
  if (check) {
    if (readFileSync(output, "utf8").replaceAll("\r\n", "\n") !== content) {
      throw new Error("API types are stale. Run node apps/web/scripts/generate-api-types.mjs");
    }
    console.log("API types match FastAPI OpenAPI.");
  } else {
    mkdirSync(dirname(output), { recursive: true });
    writeFileSync(output, content);
    console.log("Generated apps/web/src/generated/api-schema.d.ts");
  }
} finally {
  rmSync(temporary, { recursive: true, force: true });
}
