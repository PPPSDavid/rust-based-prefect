import { useCallback, useEffect, useMemo, useState } from "react";
import type { FlowRunDag } from "../types";
import { computeHighlight, findMatchingNodeIds } from "../dag/dagPathHighlight";
import { NODE_HEIGHT, NODE_WIDTH, layoutDag } from "../dag/dagLayout";
import { useDagViewport } from "../dag/useDagViewport";

const stateColor: Record<string, string> = {
  COMPLETED: "#2b9155",
  RUNNING: "#2d6cdf",
  FAILED: "#a43b3b",
  CANCELLED: "#7f4aa6",
  PENDING: "#59617d",
  SCHEDULED: "#59617d",
  NOT_REACHABLE: "#7a7f90"
};

type Props = {
  dag: FlowRunDag;
  mode: "logical" | "expanded";
  onModeChange: (mode: "logical" | "expanded") => void;
};

export function RunDagPanel({ dag, mode, onModeChange }: Props) {
  const layout = useMemo(() => layoutDag(dag.nodes, dag.edges), [dag.nodes, dag.edges]);
  const positioned = layout.nodes;
  const byId = useMemo(() => new Map(positioned.map((n) => [n.id, n])), [positioned]);
  const { containerRef, contentRef, fitAll, resetView, zoomToBounds } = useDagViewport();

  const [search, setSearch] = useState("");
  const [focusedNodeId, setFocusedNodeId] = useState<string | null>(null);
  const [matchIndex, setMatchIndex] = useState(0);

  const matches = useMemo(() => findMatchingNodeIds(dag.nodes, search), [dag.nodes, search]);
  const activeMatchId = matches.length > 0 ? matches[matchIndex % matches.length] : null;
  const highlightTarget = focusedNodeId ?? activeMatchId;
  const highlight = useMemo(
    () => computeHighlight(highlightTarget, dag.edges),
    [highlightTarget, dag.edges]
  );

  const svgWidth = Math.max(900, layout.bounds.maxX + 40);
  const svgHeight = Math.max(400, layout.bounds.maxY + 40);

  const focusNode = useCallback(
    (nodeId: string) => {
      const node = byId.get(nodeId);
      if (!node) return;
      setFocusedNodeId(nodeId);
      zoomToBounds(
        {
          minX: node.x - 24,
          minY: node.y - 24,
          maxX: node.x + NODE_WIDTH + 24,
          maxY: node.y + NODE_HEIGHT + 24,
          width: NODE_WIDTH + 48,
          height: NODE_HEIGHT + 48
        },
        24,
        true
      );
    },
    [byId, zoomToBounds]
  );

  useEffect(() => {
    if (!activeMatchId) return;
    focusNode(activeMatchId);
  }, [activeMatchId, focusNode]);

  useEffect(() => {
    setMatchIndex(0);
    if (!search.trim()) setFocusedNodeId(null);
  }, [search]);

  useEffect(() => {
    fitAll(layout.bounds);
  }, [dag.flow_run_id, mode, fitAll, layout.bounds]);

  const onSearchKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" && matches.length > 0) {
      event.preventDefault();
      const next = (matchIndex + 1) % matches.length;
      setMatchIndex(next);
      setFocusedNodeId(matches[next]);
    }
    if (event.key === "Escape") {
      setSearch("");
      setFocusedNodeId(null);
    }
  };

  const hasHighlight = highlight.nodeIds.size > 0;

  return (
    <div className="dag-panel">
      <div className="dag-toolbar">
        <div className="dag-toolbar-left">
          <button type="button" disabled={mode === "logical"} onClick={() => onModeChange("logical")}>
            Logical
          </button>
          <button type="button" disabled={mode === "expanded"} onClick={() => onModeChange("expanded")}>
            Expanded
          </button>
          <button type="button" className="dag-ghost-btn" onClick={() => fitAll(layout.bounds)}>
            Fit
          </button>
          <button type="button" className="dag-ghost-btn" onClick={resetView}>
            Reset
          </button>
        </div>
        <div className="dag-search-wrap">
          <input
            className="dag-search"
            type="search"
            placeholder="Search task runs (id, name, label)…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            onKeyDown={onSearchKeyDown}
            aria-label="Search DAG task runs"
          />
          {matches.length > 1 ? (
            <span className="dag-search-meta">
              {matchIndex + 1}/{matches.length} · Enter for next
            </span>
          ) : null}
        </div>
        <div className="dag-meta">
          source: <b>{dag.source}</b> {dag.fallback_required ? "(fallback)" : ""} · {layout.orientation}
        </div>
      </div>
      {dag.warnings.length > 0 ? <p className="dag-warning">{dag.warnings[0]}</p> : null}
      <div className="dag-legend">
        {Object.entries(stateColor).map(([state, color]) => (
          <span key={state}>
            <i style={{ background: color }} /> {state}
          </span>
        ))}
        {hasHighlight ? (
          <span className="dag-legend-highlight">
            <i /> Path highlight
          </span>
        ) : null}
      </div>
      <p className="dag-hint">Scroll to zoom · drag to pan · click a node to focus path</p>
      <div ref={containerRef} className="dag-canvas dag-canvas-interactive">
        <div ref={contentRef} className="dag-canvas-content">
          <svg width={svgWidth} height={svgHeight} className="dag-svg">
            {dag.edges.map((edge, idx) => {
              const from = byId.get(edge.from);
              const to = byId.get(edge.to);
              if (!from || !to) return null;
              const edgeKey = `${edge.from}->${edge.to}`;
              const onPath = highlight.edgeKeys.has(edgeKey);
              const dimmed = hasHighlight && !onPath;
              return (
                <line
                  key={`${edge.from}-${edge.to}-${idx}`}
                  x1={from.x + NODE_WIDTH}
                  y1={from.y + NODE_HEIGHT / 2}
                  x2={to.x}
                  y2={to.y + NODE_HEIGHT / 2}
                  stroke={onPath ? "#7eb6ff" : "#556082"}
                  strokeWidth={onPath ? 2.5 : 1.5}
                  strokeOpacity={dimmed ? 0.15 : 1}
                />
              );
            })}
            {positioned.map((node) => {
              const onPath = highlight.nodeIds.has(node.id);
              const isFocus = highlightTarget === node.id;
              const dimmed = hasHighlight && !onPath;
              return (
                <g
                  key={node.id}
                  className="dag-node"
                  onClick={() => focusNode(node.id)}
                  style={{ cursor: "pointer" }}
                >
                  <rect
                    x={node.x}
                    y={node.y}
                    width={NODE_WIDTH}
                    height={NODE_HEIGHT}
                    rx={8}
                    fill={dimmed ? "#141a2c" : "#1b2238"}
                    stroke={isFocus ? "#f0c674" : onPath ? "#7eb6ff" : (stateColor[node.state] ?? "#556082")}
                    strokeWidth={isFocus ? 3 : onPath ? 2.5 : 2}
                    opacity={dimmed ? 0.35 : 1}
                  />
                  <text
                    x={node.x + 10}
                    y={node.y + 18}
                    fill={dimmed ? "#6f7d9c" : "#e9eefc"}
                    fontSize={12}
                  >
                    {truncate(node.label, 22)}
                  </text>
                  <text
                    x={node.x + 10}
                    y={node.y + 34}
                    fill={dimmed ? "#56627a" : "#9db2d8"}
                    fontSize={11}
                  >
                    {node.state}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>
      </div>
    </div>
  );
}

function truncate(value: string, max: number) {
  return value.length <= max ? value : `${value.slice(0, max - 1)}…`;
}
