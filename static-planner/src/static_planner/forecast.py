from __future__ import annotations

from collections import defaultdict, deque
from math import ceil

from .ir import GraphIR


def forecast_graph(graph: GraphIR) -> dict:
    node_ids = [n.node_id for n in graph.nodes]
    indegree = {nid: 0 for nid in node_ids}
    outgoing: dict[str, list[str]] = defaultdict(list)

    for node in graph.nodes:
        for dep in node.deps:
            outgoing[dep].append(node.node_id)
            indegree[node.node_id] += 1

    queue = deque([nid for nid, d in indegree.items() if d == 0])
    topo: list[str] = []
    while queue:
        n = queue.popleft()
        topo.append(n)
        for m in outgoing[n]:
            indegree[m] -= 1
            if indegree[m] == 0:
                queue.append(m)

    if len(topo) != len(node_ids):
        return {
            "task_count": len(node_ids),
            "edge_count": sum(len(n.deps) for n in graph.nodes),
            "critical_path_length": None,
            "estimated_parallelism": None,
            "cycle_detected": True,
        }

    distance = {nid: 1 for nid in topo}
    by_id = {n.node_id: n for n in graph.nodes}
    for nid in topo:
        for dep in by_id[nid].deps:
            distance[nid] = max(distance[nid], distance[dep] + 1)

    critical_path = max(distance.values(), default=0)
    task_count = len(node_ids)

    return {
        "task_count": task_count,
        "edge_count": sum(len(n.deps) for n in graph.nodes),
        "critical_path_length": critical_path,
        "estimated_parallelism": ceil(task_count / critical_path) if critical_path else 0,
        "cycle_detected": False,
    }
