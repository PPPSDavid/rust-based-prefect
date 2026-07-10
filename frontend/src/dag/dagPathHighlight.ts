import type { DagEdge, DagNode } from "../types";

export function findMatchingNodeIds(nodes: DagNode[], query: string): string[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  return nodes
    .filter(
      (node) =>
        node.id.toLowerCase().includes(q) ||
        node.label.toLowerCase().includes(q) ||
        (node.task_name?.toLowerCase().includes(q) ?? false)
    )
    .map((node) => node.id);
}

function collectUpstream(targetId: string, edges: DagEdge[]): Set<string> {
  const incoming = new Map<string, string[]>();
  for (const edge of edges) {
    incoming.set(edge.to, [...(incoming.get(edge.to) ?? []), edge.from]);
  }
  const seen = new Set<string>();
  const stack = [targetId];
  while (stack.length > 0) {
    const id = stack.pop()!;
    for (const parent of incoming.get(id) ?? []) {
      if (seen.has(parent)) continue;
      seen.add(parent);
      stack.push(parent);
    }
  }
  return seen;
}

function collectDownstream(targetId: string, edges: DagEdge[]): Set<string> {
  const outgoing = new Map<string, string[]>();
  for (const edge of edges) {
    outgoing.set(edge.from, [...(outgoing.get(edge.from) ?? []), edge.to]);
  }
  const seen = new Set<string>();
  const stack = [targetId];
  while (stack.length > 0) {
    const id = stack.pop()!;
    for (const child of outgoing.get(id) ?? []) {
      if (seen.has(child)) continue;
      seen.add(child);
      stack.push(child);
    }
  }
  return seen;
}

export function computeHighlight(
  targetId: string | null,
  edges: DagEdge[]
): { nodeIds: Set<string>; edgeKeys: Set<string> } {
  if (!targetId) {
    return { nodeIds: new Set(), edgeKeys: new Set() };
  }

  const nodeIds = new Set<string>([
    targetId,
    ...collectUpstream(targetId, edges),
    ...collectDownstream(targetId, edges)
  ]);

  const edgeKeys = new Set<string>();
  for (const edge of edges) {
    if (nodeIds.has(edge.from) && nodeIds.has(edge.to)) {
      edgeKeys.add(`${edge.from}->${edge.to}`);
    }
  }

  return { nodeIds, edgeKeys };
}
