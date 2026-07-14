import { describe, expect, it } from "vitest";
import { formatTaskResult, parseTaskResultSummary } from "./artifactResult";

describe("parseTaskResultSummary", () => {
  it("reads JSON-safe result and cache_hit", () => {
    const parsed = parseTaskResultSummary(
      JSON.stringify({ task_name: "expensive", result: { x: 1 }, persisted: true, cache_hit: true })
    );
    expect(parsed.hasResult).toBe(true);
    expect(parsed.result).toEqual({ x: 1 });
    expect(parsed.cacheHit).toBe(true);
  });

  it("treats explicit null result as present", () => {
    const parsed = parseTaskResultSummary(
      JSON.stringify({ task_name: "setup", result: null, persisted: true })
    );
    expect(parsed.hasResult).toBe(true);
    expect(parsed.result).toBeNull();
  });

  it("ignores metadata-only summaries", () => {
    const parsed = parseTaskResultSummary(JSON.stringify({ task_name: "volatile" }));
    expect(parsed.hasResult).toBe(false);
  });
});

describe("formatTaskResult", () => {
  it("pretty-prints objects", () => {
    expect(formatTaskResult({ a: 1 })).toContain('"a": 1');
  });
});
