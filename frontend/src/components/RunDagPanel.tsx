import { useMemo } from "react";
import type { DagEdge, DagNode, FlowRunDag } from "../types";

const stateColor: Record<string, string> = {
  COMPLETED: "#2b9155",
  RUNNING: "#2d6cdf",
  FAILED: "#a43b3b",
  CANCELLED: "#7f4aa6",
  PENDING: "#59617d",
  SCHEDULED: "#59617d",
  NOT_REACHABLE: "#7a7f90"
};

type PositionedNode = DagNode & { x: number; y: number };

function layout(nodes: DagNode[], edges: DagEdge[]): PositionedNode[] {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const indegree = new Map<string, number>(nodes.map((n) => [n.id, 0]));
  const incoming = new Map<string, string[]>();
  for (const edge of edges) {
    if (!byId.has(edge.from) || !byId.has(edge.to)) continue;
    indegree.set(edge.to, (indegree.get(edge.to) ?? 0) + 1);
    incoming.set(edge.to, [...(incoming.get(edge.to) ?? []), edge.from]);
  }

  const queue = nodes.filter((n) => (indegree.get(n.id) ?? 0) === 0).map((n) => n.id);
  const depth = new Map<string, number>(nodes.map((n) => [n.id, 0]));

  while (queue.length > 0) {
    const id = queue.shift()!;
    const fromDepth = depth.get(id) ?? 0;
    for (const edge of edges) {
      if (edge.from !== id) continue;
      const next = edge.to;
      if (!byId.has(next)) continue;
      depth.set(next, Math.max(depth.get(next) ?? 0, fromDepth + 1));
      indegree.set(next, (indegree.get(next) ?? 1) - 1);
      if ((indegree.get(next) ?? 0) === 0) queue.push(next);
    }
  }

  const lanes = new Map<number, DagNode[]>();
  for (const node of nodes) {
    const d = depth.get(node.id) ?? 0;
    lanes.set(d, [...(lanes.get(d) ?? []), node]);
  }

  const out: PositionedNode[] = [];
  for (const [lane, laneNodes] of [...lanes.entries()].sort((a, b) => a[0] - b[0])) {
    laneNodes.forEach((node, idx) => {
      out.push({ ...node, x: lane * 220 + 20, y: idx * 80 + 20 });
    });
  }
  return out;
}

type Props = {
  dag: FlowRunDag;
  mode: "logical" | "expanded";
  onModeChange: (mode: "logical" | "expanded") => void;
};

export function RunDagPanel({ dag, mode, onModeChange }: Props) {
  const positioned = useMemo(() => layout(dag.nodes, dag.edges), [dag.nodes, dag.edges]);
  const byId = useMemo(() => new Map(positioned.map((n) => [n.id, n])), [positioned]);
  const width = Math.max(900, ...positioned.map((n) => n.x + 220));
  const height = Math.max(400, ...positioned.map((n) => n.y + 100));

  return (
    <div>
      <div className="dag-toolbar">
        <div>
          <button disabled={mode === "logical"} onClick={() => onModeChange("logical")}>
            Logical
          </button>
          <button disabled={mode === "expanded"} onClick={() => onModeChange("expanded")}>
            Expanded
          </button>
        </div>
        <div className="dag-meta">
          source: <b>{dag.source}</b> {dag.fallback_required ? "(fallback)" : ""}
        </div>
      </div>
      {dag.warnings.length > 0 && (
        <p className="dag-warning">{dag.warnings[0]}</p>
      )}
      <div className="dag-legend">
        {Object.entries(stateColor).map(([state, color]) => (
          <span key={state}>
            <i style={{ background: color }} /> {state}
          </span>
        ))}
      </div>
      <div className="dag-canvas">
        <svg width={width} height={height}>
          {dag.edges.map((edge, idx) => {
            const from = byId.get(edge.from);
            const to = byId.get(edge.to);
            if (!from || !to) return null;
            return (
              <line
                key={`${edge.from}-${edge.to}-${idx}`}
                x1={from.x + 180}
                y1={from.y + 22}
                x2={to.x}
                y2={to.y + 22}
                stroke="#556082"
                strokeWidth="1.5"
              />
            );
          })}
          {positioned.map((node) => (
            <g key={node.id}>
              <rect
                x={node.x}
                y={node.y}
                width={180}
                height={44}
                rx={8}
                fill="#1b2238"
                stroke={stateColor[node.state] ?? "#556082"}
                strokeWidth={2}
              />
              <text x={node.x + 10} y={node.y + 18} fill="#e9eefc" fontSize={12}>
                {node.label}
              </text>
              <text x={node.x + 10} y={node.y + 34} fill="#9db2d8" fontSize={11}>
                {node.state}
              </text>
            </g>
          ))}
        </svg>
      </div>
    </div>
  );
}
