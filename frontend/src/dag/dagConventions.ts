import type { LayoutOrientation } from "./dagLayout";

export type DagViewMode = "logical" | "expanded";

export const DAG_VIEW_MODES: Record<
  DagViewMode,
  { label: string; title: string; description: string }
> = {
  logical: {
    label: "Aggregated fan-out",
    title: "Planned graph with aggregated fan-out",
    description:
      "One node per forecast step; map() fan-out aggregates under a single planned node. Use expanded mode for per-execution task runs."
  },
  expanded: {
    label: "Task runs",
    title: "Per-execution task runs",
    description: "One node per task run, including every mapped child and each submit invocation."
  }
};

/** Dependencies always flow left → right; parallel siblings stack top → bottom. */
export const DAG_LAYOUT_FLOW_LABEL =
  "Dependencies: left → right · parallel tasks: top → bottom";

export function layoutFlowLabel(_orientation: LayoutOrientation = "horizontal"): string {
  return DAG_LAYOUT_FLOW_LABEL;
}

export function edgeEndpoints(
  from: { x: number; y: number },
  to: { x: number; y: number },
  _orientation: LayoutOrientation,
  nodeWidth: number,
  nodeHeight: number
): { x1: number; y1: number; x2: number; y2: number } {
  return {
    x1: from.x + nodeWidth,
    y1: from.y + nodeHeight / 2,
    x2: to.x,
    y2: to.y + nodeHeight / 2
  };
}
