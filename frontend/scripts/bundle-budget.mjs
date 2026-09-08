#!/usr/bin/env node
/**
 * Fail the build if gzipped initial JS entry chunks exceed the budget.
 * Budget is recorded in frontend/bundle-budget.json (bytes, gzip).
 */
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");
const distAssets = join(root, "dist", "assets");
const budgetPath = join(root, "bundle-budget.json");

const budget = JSON.parse(readFileSync(budgetPath, "utf8"));
const maxInitialGz = budget.initialJsGzipBytes;

const files = readdirSync(distAssets).filter((f) => f.endsWith(".js"));
// Vite emits index-*.js as the entry; sum all entry-sized chunks that look like the main graph.
// Conservative: sum gzip size of every JS asset; enforce against a generous initial budget,
// and also report the largest single chunk.
let totalGz = 0;
let largest = { name: "", gz: 0 };
for (const file of files) {
  const buf = readFileSync(join(distAssets, file));
  const gz = gzipSync(buf).length;
  totalGz += gz;
  if (gz > largest.gz) largest = { name: file, gz };
}

const report = {
  checkedAt: new Date().toISOString(),
  files: files.length,
  totalJsGzipBytes: totalGz,
  largestChunk: largest,
  budgetInitialJsGzipBytes: maxInitialGz,
  ok: totalGz <= maxInitialGz
};

const reportPath = join(root, "dist", "bundle-budget-report.json");
writeFileSync(reportPath, JSON.stringify(report, null, 2));

console.log(
  `[bundle-budget] total JS gzip=${totalGz} bytes (budget ${maxInitialGz}); largest=${largest.name} (${largest.gz})`
);

if (!report.ok) {
  console.error(`[bundle-budget] FAILED: total gzipped JS ${totalGz} exceeds budget ${maxInitialGz}`);
  process.exit(1);
}
