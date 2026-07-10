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
      "One node per forecast step. map() fan-out and repeated submits appear as separate planned steps, not individual executions."
  },
  expanded: {
    label: "Task runs",
    title: "Per-execution task runs",
    description: "One node per task run, including every mapped child and each submit invocation."
  }
};

/** Human-readable dependency flow for the active auto-layout. */
export function layoutFlowLabel(orientation: LayoutOrientation): string {
  if (orientation === "vertical") {
    return "Dependencies: top → bottom · parallel branches: left → right";
  }
  return "Dependencies: left → right · parallel tasks: top → bottom";
}

export function edgeEndpoints(
  from: { x: number; y: number },
  to: { x: number; y: number },
  orientation: LayoutOrientation,
  nodeWidth: number,
  nodeHeight: number
): { x1: number; y1: number; x2: number; y2: number } {
  if (orientation === "vertical") {
    return {
      x1: from.x + nodeWidth / 2,
      y1: from.y + nodeHeight,
      x2: to.x + nodeWidth / 2,
      y2: to.y
    };
  }
  return {
    x1: from.x + nodeWidth,
    y1: from.y + nodeHeight / 2,
    x2: to.x,
    y2: to.y + nodeHeight / 2
  };
}
