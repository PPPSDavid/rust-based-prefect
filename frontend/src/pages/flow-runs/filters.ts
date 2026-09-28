export const BUILT_IN_VIEWS = [
  { id: "running", label: "Running", state: "RUNNING" },
  { id: "failed", label: "Failed", state: "FAILED" },
  { id: "scheduled", label: "Scheduled", state: "SCHEDULED" }
] as const;

export type BuiltInViewId = (typeof BUILT_IN_VIEWS)[number]["id"];

export const STATE_CHIPS = [
  "RUNNING",
  "PENDING",
  "SCHEDULED",
  "PAUSED",
  "COMPLETED",
  "FAILED",
  "CANCELLED"
] as const;

export type TimeRange = "any" | "1h" | "24h" | "7d" | "custom";

export type RunListFilters = {
  view: BuiltInViewId | null;
  state: string | null;
  q: string;
  range: TimeRange;
  after: string;
  before: string;
};

export type FlowRunListRequest = {
  cursor?: string;
  state?: string;
  q?: string;
  createdAfter?: string;
  createdBefore?: string;
};

const STATE_SET = new Set<string>(STATE_CHIPS);

const VIEW_BY_STATE: Record<string, BuiltInViewId> = {
  RUNNING: "running",
  FAILED: "failed",
  SCHEDULED: "scheduled"
};

const RANGE_MS: Record<Exclude<TimeRange, "any" | "custom">, number> = {
  "1h": 60 * 60 * 1000,
  "24h": 24 * 60 * 60 * 1000,
  "7d": 7 * 24 * 60 * 60 * 1000
};

export function filtersFromSearchParams(params: URLSearchParams): RunListFilters {
  const view = parseView(params.get("view"));
  const rawState = (params.get("state") ?? "").trim().toUpperCase();
  const state = view ? null : STATE_SET.has(rawState) ? rawState : null;
  return {
    view,
    state,
    q: params.get("q") ?? "",
    range: parseRange(params.get("range")),
    after: params.get("after") ?? "",
    before: params.get("before") ?? ""
  };
}

export function filtersToSearchParams(filters: RunListFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.view) {
    params.set("view", filters.view);
  } else if (filters.state) {
    params.set("state", filters.state);
  }
  const q = filters.q.trim();
  if (q) params.set("q", q);
  if (filters.range !== "any") params.set("range", filters.range);
  if (filters.range === "custom") {
    if (filters.after) params.set("after", filters.after);
    if (filters.before) params.set("before", filters.before);
  }
  return params;
}

export function resolvedState(filters: RunListFilters): string | undefined {
  if (filters.view) {
    return BUILT_IN_VIEWS.find((view) => view.id === filters.view)?.state;
  }
  return filters.state ?? undefined;
}

export function withStateChip(filters: RunListFilters, state: string | null): RunListFilters {
  if (!state) {
    return { ...filters, view: null, state: null };
  }
  const view = VIEW_BY_STATE[state] ?? null;
  return { ...filters, view, state: view ? null : state };
}

export function toApiQuery(filters: RunListFilters, now: Date, cursor?: string): FlowRunListRequest {
  const window = timeWindow(filters, now);
  const q = filters.q.trim();
  return {
    cursor,
    state: resolvedState(filters),
    q: q || undefined,
    createdAfter: window.createdAfter,
    createdBefore: window.createdBefore
  };
}

function timeWindow(filters: RunListFilters, now: Date): { createdAfter?: string; createdBefore?: string } {
  if (filters.range === "1h" || filters.range === "24h" || filters.range === "7d") {
    return { createdAfter: new Date(now.getTime() - RANGE_MS[filters.range]).toISOString() };
  }
  if (filters.range === "custom") {
    return { createdAfter: toIso(filters.after), createdBefore: toIso(filters.before) };
  }
  return {};
}

function toIso(value: string): string | undefined {
  const text = value.trim();
  if (!text) return undefined;
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return undefined;
  return date.toISOString();
}

function parseView(raw: string | null): BuiltInViewId | null {
  if (raw === "running" || raw === "failed" || raw === "scheduled") return raw;
  return null;
}

function parseRange(raw: string | null): TimeRange {
  if (raw === "1h" || raw === "24h" || raw === "7d" || raw === "custom") return raw;
  return "any";
}
