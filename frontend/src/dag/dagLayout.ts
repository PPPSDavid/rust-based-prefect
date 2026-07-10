import type { DagEdge, DagNode } from "../types";

export const NODE_WIDTH = 180;
export const NODE_HEIGHT = 44;

export type PositionedNode = DagNode & { x: number; y: number };

export type DagBounds = {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
  width: number;
  height: number;
};

export type LayoutOrientation = "horizontal";

export type LayoutResult = {
  nodes: PositionedNode[];
  bounds: DagBounds;
  orientation: LayoutOrientation;
};

function computeDepths(nodes: DagNode[], edges: DagEdge[]): Map<string, number> {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const indegree = new Map(nodes.map((n) => [n.id, 0]));

  for (const edge of edges) {
    if (!byId.has(edge.from) || !byId.has(edge.to)) continue;
    indegree.set(edge.to, (indegree.get(edge.to) ?? 0) + 1);
  }

  const queue = nodes.filter((n) => (indegree.get(n.id) ?? 0) === 0).map((n) => n.id);
  const depth = new Map<string, number>(nodes.map((n) => [n.id, 0]));
  const indegreeWork = new Map(indegree);

  while (queue.length > 0) {
    const id = queue.shift()!;
    const fromDepth = depth.get(id) ?? 0;
    for (const edge of edges) {
      if (edge.from !== id) continue;
      const next = edge.to;
      if (!byId.has(next)) continue;
      depth.set(next, Math.max(depth.get(next) ?? 0, fromDepth + 1));
      indegreeWork.set(next, (indegreeWork.get(next) ?? 1) - 1);
      if ((indegreeWork.get(next) ?? 0) === 0) queue.push(next);
    }
  }

  return depth;
}

function spacingForLane(maxLaneSize: number, maxDepth: number) {
  const laneSpacing = maxLaneSize > 30 ? 48 : maxLaneSize > 12 ? 56 : maxLaneSize > 6 ? 64 : 80;
  const depthSpacing = maxDepth > 24 ? 140 : maxDepth > 12 ? 180 : 220;
  return { laneSpacing, depthSpacing };
}

export function layoutDag(nodes: DagNode[], edges: DagEdge[]): LayoutResult {
  if (nodes.length === 0) {
    return {
      nodes: [],
      bounds: { minX: 0, minY: 0, maxX: 900, maxY: 400, width: 900, height: 400 },
      orientation: "horizontal"
    };
  }

  const depth = computeDepths(nodes, edges);
  const lanes = new Map<number, DagNode[]>();
  for (const node of nodes) {
    const d = depth.get(node.id) ?? 0;
    lanes.set(d, [...(lanes.get(d) ?? []), node]);
  }

  const maxDepth = Math.max(...lanes.keys(), 0);
  const maxLaneSize = Math.max(...[...lanes.values()].map((lane) => lane.length), 1);
  const { laneSpacing, depthSpacing } = spacingForLane(maxLaneSize, maxDepth);

  const positioned: PositionedNode[] = [];
  for (const [lane, laneNodes] of [...lanes.entries()].sort((a, b) => a[0] - b[0])) {
    laneNodes.forEach((node, idx) => {
      positioned.push({
        ...node,
        x: lane * depthSpacing + 20,
        y: idx * laneSpacing + 20
      });
    });
  }

  const maxX = Math.max(...positioned.map((n) => n.x + NODE_WIDTH), 900);
  const maxY = Math.max(...positioned.map((n) => n.y + NODE_HEIGHT), 400);
  const minX = Math.min(...positioned.map((n) => n.x), 0);
  const minY = Math.min(...positioned.map((n) => n.y), 0);

  return {
    nodes: positioned,
    bounds: {
      minX,
      minY,
      maxX,
      maxY,
      width: maxX - minX,
      height: maxY - minY
    },
    orientation: "horizontal"
  };
}

export function nodeCenter(node: PositionedNode): { x: number; y: number } {
  return { x: node.x + NODE_WIDTH / 2, y: node.y + NODE_HEIGHT / 2 };
}
