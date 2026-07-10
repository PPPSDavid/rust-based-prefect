import { describe, expect, it } from "vitest";
import { DAG_VIEW_MODES, edgeEndpoints, layoutFlowLabel } from "./dagConventions";

describe("dagConventions", () => {
  it("describes horizontal flow as left to right", () => {
    expect(layoutFlowLabel("horizontal")).toContain("left → right");
  });

  it("describes vertical flow as top to bottom", () => {
    expect(layoutFlowLabel("vertical")).toContain("top → bottom");
  });

  it("labels logical mode as aggregated fan-out", () => {
    expect(DAG_VIEW_MODES.logical.label).toBe("Aggregated fan-out");
  });

  it("routes vertical edges from bottom to top", () => {
    const pts = edgeEndpoints({ x: 20, y: 20 }, { x: 20, y: 120 }, "vertical", 180, 44);
    expect(pts.y1).toBeGreaterThan(pts.y2 - 120);
    expect(pts.x1).toBe(pts.x2);
  });
});
