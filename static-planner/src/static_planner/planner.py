from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

from .forecast import forecast_graph
from .ir import GraphIR, TaskNode


@dataclass
class CompileDiagnostics:
    warnings: list[str]
    fallback_required: bool


def compile_flow_source(source: str, flow_name: str = "flow") -> tuple[GraphIR, CompileDiagnostics]:
    tree = ast.parse(source)
    nodes: list[TaskNode] = []
    bound_nodes: dict[str, str] = {}
    warnings: list[str] = []
    fallback_required = False
    counter = 0

    def visit_stmt(stmt: ast.stmt) -> None:
        nonlocal counter, fallback_required
        if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
            maybe = _extract_task_call(stmt.value, bound_nodes)
            if maybe is not None and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                counter += 1
                var_name = stmt.targets[0].id
                node = TaskNode(
                    node_id=f"n{counter}",
                    task_name=maybe["task_name"],
                    op_type=maybe["op_type"],
                    deps=maybe["deps"],
                )
                nodes.append(node)
                bound_nodes[var_name] = node.node_id
                return

        if isinstance(stmt, ast.For):
            unrolled = _bounded_range(stmt.iter)
            if unrolled is None:
                warnings.append("Non-bounded loop detected; fallback required.")
                fallback_required = True
                return
            for _ in range(unrolled):
                for inner in stmt.body:
                    visit_stmt(inner)
            return

        if isinstance(stmt, ast.If):
            warnings.append("Conditional detected; path-sensitive compile is limited.")
            fallback_required = True
            return

        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            maybe = _extract_task_call(stmt.value, bound_nodes)
            if maybe is not None:
                counter += 1
                nodes.append(
                    TaskNode(
                        node_id=f"n{counter}",
                        task_name=maybe["task_name"],
                        op_type=maybe["op_type"],
                        deps=maybe["deps"],
                    )
                )
                return

    for stmt in tree.body:
        visit_stmt(stmt)

    graph = GraphIR(flow_name=flow_name, nodes=nodes)
    return graph, CompileDiagnostics(warnings=warnings, fallback_required=fallback_required)


def compile_and_forecast(source: str, flow_name: str = "flow") -> dict[str, Any]:
    graph, diagnostics = compile_flow_source(source, flow_name)
    forecast = forecast_graph(graph)
    return {
        "manifest": graph.as_manifest(),
        "forecast": forecast,
        "diagnostics": {
            "warnings": diagnostics.warnings,
            "fallback_required": diagnostics.fallback_required,
        },
    }


def _extract_task_call(call: ast.Call, bound_nodes: dict[str, str]) -> dict[str, Any] | None:
    if not isinstance(call.func, ast.Attribute):
        return None
    attr = call.func.attr
    if attr not in {"submit", "map"}:
        return None

    task_name = "unknown_task"
    if isinstance(call.func.value, ast.Name):
        task_name = call.func.value.id

    deps: list[str] = []
    for arg in call.args:
        if isinstance(arg, ast.Name) and arg.id in bound_nodes:
            deps.append(bound_nodes[arg.id])

    return {"task_name": task_name, "op_type": attr, "deps": deps}


def _bounded_range(node: ast.AST) -> int | None:
    if not isinstance(node, ast.Call):
        return None
    if not isinstance(node.func, ast.Name) or node.func.id != "range":
        return None
    if len(node.args) == 1 and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, int):
        value = node.args[0].value
        return value if value >= 0 else None
    return None
