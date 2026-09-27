import { describe, expect, it } from "vitest";
import {
  filtersFromSearchParams,
  filtersToSearchParams,
  resolvedState,
  toApiQuery,
  withStateChip
} from "./filters";

const empty = {
  view: null,
  state: null,
  q: "",
  range: "any" as const,
  after: "",
  before: ""
};

describe("flow run list filters", () => {
  it("maps built-in views to state and keeps the URL shareable", () => {
    const filters = filtersFromSearchParams(new URLSearchParams("view=failed&q=nightly"));
    expect(filters.view).toBe("failed");
    expect(resolvedState(filters)).toBe("FAILED");
    const params = filtersToSearchParams(filters);
    expect(params.get("view")).toBe("failed");
    expect(params.get("state")).toBeNull();
    expect(params.get("q")).toBe("nightly");
  });

  it("keeps paused as an explicit state chip", () => {
    const next = withStateChip(empty, "PAUSED");
    expect(resolvedState(next)).toBe("PAUSED");
    expect(filtersToSearchParams(next).toString()).toBe("state=PAUSED");
  });

  it("turns Running, Failed, and Scheduled chips into named views", () => {
    const next = withStateChip(empty, "SCHEDULED");
    expect(next.view).toBe("scheduled");
    expect(filtersToSearchParams(next).get("view")).toBe("scheduled");
  });

  it("evaluates relative time ranges from now and sends them to the API", () => {
    const now = new Date("2026-04-15T12:00:00.000Z");
    const filters = filtersFromSearchParams(new URLSearchParams("range=1h&view=running"));
    expect(toApiQuery(filters, now)).toEqual({
      cursor: undefined,
      state: "RUNNING",
      q: undefined,
      createdAfter: "2026-04-15T11:00:00.000Z",
      createdBefore: undefined
    });
  });

  it("lets a named view win over a conflicting state param", () => {
    const filters = filtersFromSearchParams(new URLSearchParams("view=failed&state=RUNNING"));
    expect(resolvedState(filters)).toBe("FAILED");
  });
});
