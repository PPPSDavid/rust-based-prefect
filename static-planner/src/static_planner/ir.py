from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TaskNode:
    node_id: str
    task_name: str
    op_type: str
    deps: list[str] = field(default_factory=list)


@dataclass
class GraphIR:
    flow_name: str
    nodes: list[TaskNode]

    def as_manifest(self) -> dict:
        edges = []
        for node in self.nodes:
            for dep in node.deps:
                edges.append({"from": dep, "to": node.node_id})
        return {
            "flow_name": self.flow_name,
            "nodes": [n.__dict__ for n in self.nodes],
            "edges": edges,
        }
