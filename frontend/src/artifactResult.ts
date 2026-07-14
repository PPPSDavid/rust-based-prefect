/** Parse persisted task result from an artifact summary JSON string. */

export function parseTaskResultSummary(
  summary: string | null | undefined
): { hasResult: boolean; result: unknown; cacheHit: boolean } {
  if (!summary) {
    return { hasResult: false, result: undefined, cacheHit: false };
  }
  try {
    const parsed: unknown = JSON.parse(summary);
    if (parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)) {
      const obj = parsed as Record<string, unknown>;
      const cacheHit = obj.cache_hit === true;
      if ("result" in obj) {
        return { hasResult: true, result: obj.result, cacheHit };
      }
      if (obj.persisted === true && !("result" in obj)) {
        return { hasResult: false, result: undefined, cacheHit };
      }
    }
  } catch {
    /* not JSON */
  }
  return { hasResult: false, result: undefined, cacheHit: false };
}

export function formatTaskResult(result: unknown): string {
  if (result === undefined) {
    return "";
  }
  try {
    return JSON.stringify(result, null, 2);
  } catch {
    return String(result);
  }
}
