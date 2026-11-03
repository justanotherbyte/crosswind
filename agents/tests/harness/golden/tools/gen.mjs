// Regenerates the "expected" field of ../apply_chunks.json with upstream's own
// applyChunks, imported from a cloudflare/agents checkout at the pinned commit
// (packages/agents/src/experimental/channels/apply-chunks.ts):
//
//   UPSTREAM_AGENTS=../agents node tests/harness/golden/tools/gen.mjs
//   node tests/harness/golden/tools/gen.mjs ../agents
//
// Needs Node 22.18 or later, which strips TypeScript types without a flag.
// On Node 22.6 to 22.17, run it with --experimental-strip-types.
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const COMMIT = "455b47ec93f99f306f644ac06bc8b72b0627c1fc";

const checkout = process.argv[2] ?? process.env.UPSTREAM_AGENTS;
if (!checkout) {
  throw new Error("Pass the cloudflare/agents checkout as an argument or in UPSTREAM_AGENTS");
}
let head;
try {
  head = execFileSync("git", ["-C", checkout, "rev-parse", "HEAD"], { encoding: "utf8" }).trim();
} catch {
  throw new Error(`${checkout} isn't a git checkout of cloudflare/agents`);
}
if (head !== COMMIT) {
  throw new Error(`${checkout} is at ${head}; check out ${COMMIT} first`);
}

const source = resolve(checkout, "packages/agents/src/experimental/channels/apply-chunks.ts");
const { applyChunks } = await import(pathToFileURL(source).href);

const path = new URL("../apply_chunks.json", import.meta.url);
const cases = JSON.parse(readFileSync(path, "utf8")).map(({ name, message, chunks }) => ({
  name,
  message,
  chunks,
  expected: JSON.parse(JSON.stringify(applyChunks(message, chunks)))
}));
writeFileSync(path, JSON.stringify(cases, null, 2) + "\n");
