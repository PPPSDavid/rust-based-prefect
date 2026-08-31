"""Graph mode resolution for static vs dynamic flow orchestration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, cast

GraphModeLiteral = Literal["auto", "static", "dynamic"]
EffectiveGraphMode = Literal["static", "dynamic"]


class StaticGraphDeclarationError(ValueError):
    """Raised when ``graph_mode='static'`` conflicts with planner diagnostics."""


class StaticGraphContractViolation(RuntimeError):
    """Raised when a static flow would allocate a dynamic planned node at runtime."""


@dataclass(frozen=True)
class GraphModeResolution:
    declared: GraphModeLiteral
    effective: EffectiveGraphMode
    fallback_required: bool
    manifest_fingerprint: str | None


def normalize_declared_graph_mode(value: str | None) -> GraphModeLiteral:
    mode = (value or "auto").strip().lower()
    if mode not in {"auto", "static", "dynamic"}:
        raise ValueError(
            f"graph_mode must be 'auto', 'static', or 'dynamic', got {value!r}"
        )
    return cast(GraphModeLiteral, mode)


def manifest_fingerprint(manifest: dict[str, Any] | None) -> str | None:
    """Stable hash of logical DAG nodes for execution-contract comparison."""
    if not manifest:
        return None
    nodes = manifest.get("nodes") or []
    if not nodes:
        return None
    canonical = []
    for node in sorted(nodes, key=lambda n: str(n.get("node_id", ""))):
        deps = node.get("deps") or []
        canonical.append(
            {
                "node_id": str(node.get("node_id", "")),
                "task_name": str(node.get("task_name", "")),
                "op_type": str(node.get("op_type", "")),
                "deps": sorted(str(d) for d in deps),
            }
        )
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_graph_mode(
    declared: GraphModeLiteral,
    *,
    fallback_required: bool,
    manifest: dict[str, Any] | None,
) -> GraphModeResolution:
    """Resolve declared graph mode using planner diagnostics."""
    fp = manifest_fingerprint(manifest)
    has_manifest = bool(manifest and manifest.get("nodes"))

    if declared == "dynamic":
        return GraphModeResolution(
            declared=declared,
            effective="dynamic",
            fallback_required=fallback_required,
            manifest_fingerprint=fp,
        )

    if declared == "static":
        if fallback_required or not has_manifest:
            raise StaticGraphDeclarationError(
                "Flow declared graph_mode='static' but static analysis requires "
                "fallback (dynamic control flow or empty manifest). "
                "Use graph_mode='dynamic' or refactor to static submit chains."
            )
        return GraphModeResolution(
            declared=declared,
            effective="static",
            fallback_required=fallback_required,
            manifest_fingerprint=fp,
        )

    # auto
    if fallback_required or not has_manifest:
        return GraphModeResolution(
            declared=declared,
            effective="dynamic",
            fallback_required=fallback_required,
            manifest_fingerprint=fp,
        )
    return GraphModeResolution(
        declared=declared,
        effective="static",
        fallback_required=fallback_required,
        manifest_fingerprint=fp,
    )


def contract_allows_resume_skips(
    *,
    effective: EffectiveGraphMode,
    parameters_match: bool,
    contract_mismatch: bool,
) -> bool:
    if effective != "static":
        return False
    if not parameters_match:
        return False
    return not contract_mismatch
