import { describe, expect, it } from "vitest";
import { computeHighlight, findMatchingNodeIds } from "./dagPathHighlight";

const nodes = [
  { id: "a", label: "start", state: "COMPLETED" },
  { id: "b", label: "mid", state: "COMPLETED" },
  { id: "c", label: "end", state: "COMPLETED" }
];
const edges = [
  { from: "a", to: "b" },
  { from: "b", to: "c" }
];

describe("dagPathHighlight", () => {
  it("finds nodes by label fragment", () => {
    expect(findMatchingNodeIds(nodes, "mid")).toEqual(["b"]);
  });

  it("highlights upstream and downstream path", () => {
    const { nodeIds, edgeKeys } = computeHighlight("b", edges);
    expect([...nodeIds].sort()).toEqual(["a", "b", "c"]);
    expect([...edgeKeys].sort()).toEqual(["a->b", "b->c"]);
  });
});
