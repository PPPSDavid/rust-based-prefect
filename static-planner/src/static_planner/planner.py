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


def _flow_statements(tree: ast.Module, flow_name: str) -> list[ast.stmt]:
    """Return statements to analyze: a single @flow body, a named function, or module-level code."""
    func_defs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if len(func_defs) == 1:
        return func_defs[0].body
    for func in func_defs:
        if func.name == flow_name:
            return func.body
    return list(tree.body)


def compile_flow_source(source: str, flow_name: str = "flow") -> tuple[GraphIR, CompileDiagnostics]:
    tree = ast.parse(source)
    nodes: list[TaskNode] = []
    bound_nodes: dict[str, str] = {}
    warnings: list[str] = []
    fallback_required = False
    counter = 0
    task_instance_counts: dict[str, int] = {}

    def _make_node(task_name: str, op_type: str, deps: list[str]) -> TaskNode:
        nonlocal counter
        counter += 1
        instance = task_instance_counts.get(task_name, 0)
        task_instance_counts[task_name] = instance + 1
        return TaskNode(
            node_id=f"n{counter}",
            task_name=task_name,
            op_type=op_type,
            deps=deps,
            label=f"{task_name}-{instance}",
        )

    def visit_stmt(stmt: ast.stmt) -> None:
        nonlocal fallback_required
        if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
            maybe = _extract_task_call(stmt.value, bound_nodes)
            if maybe is not None and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                var_name = stmt.targets[0].id
                node = _make_node(maybe["task_name"], maybe["op_type"], maybe["deps"])
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
                nodes.append(_make_node(maybe["task_name"], maybe["op_type"], maybe["deps"]))
                return

    for stmt in _flow_statements(tree, flow_name):
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

    dep_ids: list[str] = []
    seen: set[str] = set()

    def add_dep(name: str | None) -> None:
        if not name or name not in bound_nodes:
            return
        node_id = bound_nodes[name]
        if node_id not in seen:
            seen.add(node_id)
            dep_ids.append(node_id)

    for arg in call.args:
        if isinstance(arg, ast.Name):
            add_dep(arg.id)

    for kw in call.keywords:
        if kw.arg != "wait_for":
            continue
        if isinstance(kw.value, (ast.List, ast.Tuple)):
            for elt in kw.value.elts:
                if isinstance(elt, ast.Name):
                    add_dep(elt.id)

    return {"task_name": task_name, "op_type": attr, "deps": dep_ids}


def _bounded_range(node: ast.AST) -> int | None:
    if not isinstance(node, ast.Call):
        return None
    if not isinstance(node.func, ast.Name) or node.func.id != "range":
        return None
    if len(node.args) == 1 and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, int):
        value = node.args[0].value
        return value if value >= 0 else None
    return None
