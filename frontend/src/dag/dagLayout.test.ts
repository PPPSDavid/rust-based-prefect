import { describe, expect, it } from "vitest";
import { layoutDag } from "./dagLayout";

describe("layoutDag", () => {
  it("uses horizontal layout for wide fan-out", () => {
    const nodes = [
      { id: "a", label: "inc", state: "COMPLETED" },
      ...Array.from({ length: 12 }, (_, i) => ({
        id: `b${i}`,
        label: `dbl-${i}`,
        state: "COMPLETED"
      }))
    ];
    const edges = nodes.slice(1).map((n) => ({ from: "a", to: n.id }));
    const result = layoutDag(nodes, edges);
    expect(result.orientation).toBe("horizontal");
    expect(result.nodes[0].x).toBeLessThan(result.nodes[1].x);
    expect(result.nodes[2].y).toBeGreaterThan(result.nodes[1].y);
  });

  it("uses vertical layout for long serial chains", () => {
    const nodes = Array.from({ length: 12 }, (_, i) => ({
      id: `n${i}`,
      label: `inc-${i}`,
      state: "COMPLETED"
    }));
    const edges = nodes.slice(1).map((n, i) => ({ from: `n${i}`, to: n.id }));
    const result = layoutDag(nodes, edges);
    expect(result.orientation).toBe("vertical");
    expect(result.nodes[1].y).toBeGreaterThan(result.nodes[0].y);
  });
});
