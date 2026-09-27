import { describe, expect, it } from "vitest";
import type { Deployment } from "../../types";
import { filterDeployments, formatSchedule, groupDeployments } from "./deploymentGroups";

function dep(overrides: Partial<Deployment> & Pick<Deployment, "id" | "name" | "flow_name">): Deployment {
  return {
    default_parameters: {},
    paused: false,
    created_at: "2026-04-15T21:00:00+00:00",
    updated_at: "2026-04-15T21:00:00+00:00",
    schedule_enabled: false,
    ...overrides
  };
}

describe("deployment groups", () => {
  const hourly = dep({
    id: "d1",
    name: "hourly",
    flow_name: "etl",
    schedule_enabled: true,
    schedule_cron: "0 * * * *"
  });
  const nightly = dep({
    id: "d2",
    name: "nightly",
    flow_name: "etl",
    paused: true,
    schedule_enabled: true,
    schedule_interval_seconds: 3600,
    updated_at: "2026-04-16T21:00:00+00:00"
  });
  const only = dep({ id: "d3", name: "only-one", flow_name: "solo" });

  it("keeps a single deployment visible under its flow", () => {
    const groups = groupDeployments([only], { key: "recent", dir: "desc" });
    expect(groups).toEqual([{ flowName: "solo", deployments: [only] }]);
    expect(formatSchedule(only)).toBe("Manual");
    expect(formatSchedule(hourly)).toBe("cron 0 * * * *");
    expect(formatSchedule(nightly)).toBe("every 3600s");
  });

  it("groups every deployment of a flow without dropping names", () => {
    const groups = groupDeployments([hourly, nightly, only], { key: "recent", dir: "desc" });
    expect(groups.map((group) => group.flowName)).toEqual(["etl", "solo"]);
    expect(groups[0]?.deployments.map((item) => item.name)).toEqual(["hourly", "nightly"]);
  });

  it("orders groups by flow name and still lists each deployment", () => {
    const groups = groupDeployments([hourly, only], { key: "flow", dir: "desc" });
    expect(groups.map((group) => group.flowName)).toEqual(["solo", "etl"]);
    expect(groups[1]?.deployments.map((item) => item.name)).toEqual(["hourly"]);
  });

  it("matches a flow name without hiding its other deployments", () => {
    const matched = filterDeployments([hourly, nightly, only], "etl", "all");
    expect(matched.map((item) => item.name)).toEqual(["hourly", "nightly"]);
  });

  it("can match one deployment name inside a flow", () => {
    const matched = filterDeployments([hourly, nightly, only], "nightly", "all");
    expect(matched.map((item) => item.name)).toEqual(["nightly"]);
  });

  it("applies status before search so a paused sibling stays out of Active", () => {
    const matched = filterDeployments([hourly, nightly], "etl", "active");
    expect(matched.map((item) => item.name)).toEqual(["hourly"]);
  });
});
