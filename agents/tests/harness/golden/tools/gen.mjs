// Regenerates the "expected" field of ../apply_chunks.json with upstream's
// applyChunks (cloudflare/agents@455b47e, types stripped in apply-chunks.mjs):
//   node tests/harness/golden/tools/gen.mjs
import { readFileSync, writeFileSync } from "node:fs";
import { applyChunks } from "./apply-chunks.mjs";

const path = new URL("../apply_chunks.json", import.meta.url);
const cases = JSON.parse(readFileSync(path, "utf8")).map(({ name, message, chunks }) => ({
  name,
  message,
  chunks,
  expected: JSON.parse(JSON.stringify(applyChunks(message, chunks)))
}));
writeFileSync(path, JSON.stringify(cases, null, 2) + "\n");
