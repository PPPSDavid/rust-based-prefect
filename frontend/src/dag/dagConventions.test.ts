import { describe, expect, it } from "vitest";
import { DAG_LAYOUT_FLOW_LABEL, DAG_VIEW_MODES, edgeEndpoints, layoutFlowLabel } from "./dagConventions";

describe("dagConventions", () => {
  it("always describes dependency flow as left to right", () => {
    expect(layoutFlowLabel("horizontal")).toContain("left → right");
    expect(layoutFlowLabel()).toBe(DAG_LAYOUT_FLOW_LABEL);
  });

  it("labels logical mode as aggregated fan-out", () => {
    expect(DAG_VIEW_MODES.logical.label).toBe("Aggregated fan-out");
  });

  it("routes edges from right side to left side", () => {
    const pts = edgeEndpoints({ x: 20, y: 20 }, { x: 240, y: 20 }, "horizontal", 180, 44);
    expect(pts.x1).toBeGreaterThan(pts.x2 - 240);
    expect(pts.y1).toBe(pts.y2);
  });
});
