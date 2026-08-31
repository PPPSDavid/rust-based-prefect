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
    func_defs = [
        n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if len(func_defs) == 1:
        return func_defs[0].body
    for func in func_defs:
        if func.name == flow_name:
            return func.body
    return list(tree.body)


def compile_flow_source(
    source: str,
    flow_name: str = "flow",
    task_names: dict[str, str] | None = None,
) -> tuple[GraphIR, CompileDiagnostics]:
    tree = ast.parse(source)
    nodes: list[TaskNode] = []
    bound_nodes: dict[str, str] = {}
    warnings: list[str] = []
    fallback_required = False
    counter = 0
    task_instance_counts: dict[str, int] = {}

    gate_symbols: set[str] = set()
    gate_labels: dict[str, str] = {}
    subflow_symbols: dict[str, str] = {}
    name_lookup = task_names or {}

    def _resolve_task_name(symbol: str) -> str:
        return name_lookup.get(symbol, symbol)

    def _make_node(symbol: str, op_type: str, deps: list[str]) -> TaskNode:
        nonlocal counter
        counter += 1
        task_name = symbol if ":" in symbol else _resolve_task_name(symbol)
        instance = task_instance_counts.get(task_name, 0)
        task_instance_counts[task_name] = instance + 1
        return TaskNode(
            node_id=f"n{counter}",
            task_name=task_name,
            op_type=op_type,
            deps=deps,
            label=f"{task_name}-{instance}",
        )

    def _ensure_task_node_from_call(
        call: ast.Call, call_to_node: dict[int, str]
    ) -> str | None:
        """Materialize nested submit/map calls and return this call's node id."""
        existing = call_to_node.get(id(call))
        if existing is not None:
            return existing
        nested_dep_ids: list[str] = []
        for arg in call.args:
            if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute):
                if arg.func.attr in {"submit", "map"}:
                    nested_id = _ensure_task_node_from_call(arg, call_to_node)
                    if nested_id is not None and nested_id not in nested_dep_ids:
                        nested_dep_ids.append(nested_id)
        maybe = _extract_task_call(
            call, bound_nodes, gate_symbols, gate_labels, subflow_symbols, name_lookup
        )
        if maybe is None:
            return None
        deps = list(dict.fromkeys([*maybe["deps"], *nested_dep_ids]))
        node = _make_node(maybe["symbol"], maybe["op_type"], deps)
        nodes.append(node)
        call_to_node[id(call)] = node.node_id
        return node.node_id

    def _append_task_call(call: ast.Call) -> None:
        _ensure_task_node_from_call(call, {})

    def visit_stmt(stmt: ast.stmt) -> None:
        nonlocal fallback_required
        if isinstance(stmt, ast.Assign):
            if (
                len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Name)
            ):
                var_name = stmt.targets[0].id
                if stmt.value.func.id == "gate":
                    gate_symbols.add(var_name)
                    gate_label = "gate"
                    for kw in stmt.value.keywords:
                        if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                            if isinstance(kw.value.value, str):
                                gate_label = kw.value.value
                    gate_labels[var_name] = gate_label
                elif stmt.value.func.id == "deployment_ref":
                    dep_name = _deployment_ref_name(stmt.value)
                    if dep_name is not None:
                        subflow_symbols[var_name] = f"subflow:{dep_name}"
                    else:
                        warnings.append(
                            "Non-literal deployment_ref(); fallback required."
                        )
                        fallback_required = True
            call = _locate_task_call(stmt.value)
            if (
                call is not None
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
            ):
                var_name = stmt.targets[0].id
                maybe = _extract_task_call(
            call, bound_nodes, gate_symbols, gate_labels, subflow_symbols, name_lookup
        )
                if maybe is not None:
                    node_id = _ensure_task_node_from_call(call, {})
                    if node_id is not None:
                        bound_nodes[var_name] = node_id
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

        if isinstance(stmt, ast.Expr):
            call = _locate_task_call(stmt.value)
            if call is not None:
                _append_task_call(call)
                return

        if isinstance(stmt, ast.Return) and stmt.value is not None:
            map_call = _find_map_call(stmt.value)
            if map_call is not None:
                _append_task_call(map_call)
                return
            call = _locate_task_call(stmt.value)
            if call is not None:
                _append_task_call(call)
                return

    for stmt in _flow_statements(tree, flow_name):
        visit_stmt(stmt)

    if any(node.task_name == "unknown_task" for node in nodes):
        warnings.append("Unresolved task symbol; fallback required.")
        fallback_required = True

    graph = GraphIR(flow_name=flow_name, nodes=nodes)
    return graph, CompileDiagnostics(
        warnings=warnings, fallback_required=fallback_required
    )


def compile_and_forecast(
    source: str,
    flow_name: str = "flow",
    task_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    graph, diagnostics = compile_flow_source(source, flow_name, task_names=task_names)
    forecast = forecast_graph(graph)
    return {
        "manifest": graph.as_manifest(),
        "forecast": forecast,
        "diagnostics": {
            "warnings": diagnostics.warnings,
            "fallback_required": diagnostics.fallback_required,
        },
    }


def _deployment_ref_name(call: ast.Call) -> str | None:
    if not isinstance(call.func, ast.Name) or call.func.id != "deployment_ref":
        return None
    if not call.args:
        return None
    arg = call.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return None


def _submit_target_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "cast" and len(node.args) >= 2:
            inner = node.args[1]
            if isinstance(inner, ast.Name):
                return inner.id
    return None


def _extract_task_call(
    call: ast.Call,
    bound_nodes: dict[str, str],
    gate_symbols: set[str] | None = None,
    gate_labels: dict[str, str] | None = None,
    subflow_symbols: dict[str, str] | None = None,
    task_names: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(call.func, ast.Attribute):
        return None
    attr = call.func.attr
    if attr not in {"submit", "map"}:
        return None

    symbol = "unknown_task"
    op_type = attr
    target = _submit_target_name(call.func.value)
    if target is not None:
        if subflow_symbols and target in subflow_symbols:
            symbol = subflow_symbols[target]
        elif gate_symbols and target in gate_symbols and attr == "submit":
            op_type = "gate"
            label = (gate_labels or {}).get(target, target)
            symbol = f"gate:{label}"
        elif task_names and target in task_names:
            symbol = task_names[target]
        else:
            symbol = target
    elif isinstance(call.func.value, ast.Call):
        dep_name = _deployment_ref_name(call.func.value)
        if dep_name is not None:
            symbol = f"subflow:{dep_name}"
        elif (
            isinstance(call.func.value.func, ast.Name)
            and call.func.value.func.id == "gate"
        ):
            gate_label = "gate"
            for kw in call.func.value.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    if isinstance(kw.value.value, str):
                        gate_label = kw.value.value
            if attr == "submit":
                op_type = "gate"
                symbol = f"gate:{gate_label}"
        elif (
            isinstance(call.func.value.func, ast.Name)
            and call.func.value.func.id == "deployment_ref"
        ):
            symbol = "unknown_task"

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

    return {"symbol": symbol, "op_type": op_type, "deps": dep_ids}


def _find_map_call(node: ast.AST) -> ast.Call | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == "map":
            return node
    for child in ast.iter_child_nodes(node):
        found = _find_map_call(child)
        if found is not None:
            return found
    return None


def _locate_task_call(node: ast.AST) -> ast.Call | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in {"submit", "map"}:
            return node
        if node.func.attr == "result":
            return _locate_task_call(node.func.value)
    return None


def _bounded_range(node: ast.AST) -> int | None:
    if not isinstance(node, ast.Call):
        return None
    if not isinstance(node.func, ast.Name) or node.func.id != "range":
        return None
    if (
        len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, int)
    ):
        value = node.args[0].value
        return value if value >= 0 else None
    return None
