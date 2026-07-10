import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import type { FlowRunDag } from "../types";
import { computeHighlight, findMatchingNodeIds } from "../dag/dagPathHighlight";
import { NODE_HEIGHT, NODE_WIDTH, layoutDag } from "../dag/dagLayout";
import { DAG_VIEW_MODES, edgeEndpoints, layoutFlowLabel } from "../dag/dagConventions";
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

type InlineExpandState = {
  loading: boolean;
  dag?: FlowRunDag;
  error?: string;
};

export function RunDagPanel({ dag, mode, onModeChange }: Props) {
  const navigate = useNavigate();
  const layout = useMemo(() => layoutDag(dag.nodes, dag.edges), [dag.nodes, dag.edges]);
  const positioned = layout.nodes;
  const byId = useMemo(() => new Map(positioned.map((n) => [n.id, n])), [positioned]);
  const { containerRef, contentRef, fitAll, resetView, zoomToBounds } = useDagViewport();

  const [search, setSearch] = useState("");
  const [focusedNodeId, setFocusedNodeId] = useState<string | null>(null);
  const [matchIndex, setMatchIndex] = useState(0);
  const [expandedInline, setExpandedInline] = useState<Record<string, InlineExpandState>>({});

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

  const toggleInlineExpand = useCallback(async (childFlowRunId: string, nodeId: string) => {
    let shouldLoad = false;
    setExpandedInline((current) => {
      if (current[nodeId]?.dag) {
        const next = { ...current };
        delete next[nodeId];
        return next;
      }
      shouldLoad = true;
      return { ...current, [nodeId]: { loading: true } };
    });
    if (!shouldLoad) {
      return;
    }
    try {
      const childDag = await api.getFlowRunDag(childFlowRunId, "logical");
      setExpandedInline((current) => ({
        ...current,
        [nodeId]: { loading: false, dag: childDag }
      }));
    } catch {
      setExpandedInline((current) => ({
        ...current,
        [nodeId]: { loading: false, error: "Failed to load inline subflow DAG." }
      }));
    }
  }, []);

  const onNodeClick = useCallback(
    (nodeId: string) => {
      const meta = dag.nodes.find((node) => node.id === nodeId);
      if (!meta) {
        focusNode(nodeId);
        return;
      }
      if (meta.kind === "subflow_task" && meta.child_flow_run_id) {
        navigate(`/runs/${meta.child_flow_run_id}`);
        return;
      }
      if (meta.kind === "inline_subflow" && meta.child_flow_run_id) {
        void toggleInlineExpand(meta.child_flow_run_id, nodeId);
        return;
      }
      focusNode(nodeId);
    },
    [dag.nodes, focusNode, navigate, toggleInlineExpand]
  );

  const onNodeDoubleClick = useCallback(
    (nodeId: string) => {
      const meta = dag.nodes.find((node) => node.id === nodeId);
      if (meta?.kind === "inline_subflow" && meta.child_flow_run_id) {
        navigate(`/runs/${meta.child_flow_run_id}`);
      }
    },
    [dag.nodes, navigate]
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
    setExpandedInline({});
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
  const viewMode = DAG_VIEW_MODES[mode];
  const flowLabel = layoutFlowLabel(layout.orientation);
  const expandedEntries = Object.entries(expandedInline);

  return (
    <div className="dag-panel">
      <div className="dag-toolbar">
        <div className="dag-toolbar-left">
          <button
            type="button"
            className={mode === "logical" ? "dag-mode-btn active" : "dag-mode-btn"}
            disabled={mode === "logical"}
            title={DAG_VIEW_MODES.logical.title}
            onClick={() => onModeChange("logical")}
          >
            {DAG_VIEW_MODES.logical.label}
          </button>
          <button
            type="button"
            className={mode === "expanded" ? "dag-mode-btn active" : "dag-mode-btn"}
            disabled={mode === "expanded"}
            title={DAG_VIEW_MODES.expanded.title}
            onClick={() => onModeChange("expanded")}
          >
            {DAG_VIEW_MODES.expanded.label}
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
          source: <b>{dag.source}</b>
          {dag.fallback_required ? " (fallback)" : ""}
        </div>
      </div>
      <p className="dag-definition">
        <strong>{viewMode.label} view.</strong> {viewMode.description} {flowLabel}.
      </p>
      {dag.warnings.length > 0 ? <p className="dag-warning">{dag.warnings[0]}</p> : null}
      <div className="dag-legend">
        {Object.entries(stateColor).map(([state, color]) => (
          <span key={state}>
            <i style={{ background: color }} /> {state}
          </span>
        ))}
        <span>
          <i className="dag-legend-inline" /> inline subflow
        </span>
        <span>
          <i className="dag-legend-subflow-task" /> deployment subflow
        </span>
        {hasHighlight ? (
          <span className="dag-legend-highlight">
            <i /> Path highlight
          </span>
        ) : null}
      </div>
      <p className="dag-hint">
        Scroll to zoom · drag to pan · click task to focus path · click deployment subflow to open child run ·
        click inline subflow to expand · double-click inline subflow to open child run
      </p>
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
              const { x1, y1, x2, y2 } = edgeEndpoints(from, to, layout.orientation, NODE_WIDTH, NODE_HEIGHT);
              return (
                <line
                  key={`${edge.from}-${edge.to}-${idx}`}
                  x1={x1}
                  y1={y1}
                  x2={x2}
                  y2={y2}
                  stroke={onPath ? "#7eb6ff" : "#556082"}
                  strokeWidth={onPath ? 2.5 : 1.5}
                  strokeOpacity={dimmed ? 0.15 : 1}
                />
              );
            })}
            {positioned.map((node) => {
              const meta = dag.nodes.find((item) => item.id === node.id);
              const kind = meta?.kind ?? "task";
              const onPath = highlight.nodeIds.has(node.id);
              const isFocus = highlightTarget === node.id;
              const dimmed = hasHighlight && !onPath;
              const isExpanded = Boolean(expandedInline[node.id]?.dag);
              const nodeClass =
                kind === "inline_subflow"
                  ? "dag-node dag-node-inline-subflow"
                  : kind === "subflow_task"
                    ? "dag-node dag-node-subflow-task"
                    : "dag-node";
              const strokeColor =
                kind === "inline_subflow"
                  ? isExpanded
                    ? "#9ad4a0"
                    : "#5f9f6a"
                  : kind === "subflow_task"
                    ? "#c9a227"
                    : isFocus
                      ? "#f0c674"
                      : onPath
                        ? "#7eb6ff"
                        : (stateColor[node.state] ?? "#556082");
              return (
                <g
                  key={node.id}
                  className={nodeClass}
                  onClick={() => onNodeClick(node.id)}
                  onDoubleClick={() => onNodeDoubleClick(node.id)}
                  style={{ cursor: "pointer" }}
                >
                  <rect
                    x={node.x}
                    y={node.y}
                    width={NODE_WIDTH}
                    height={NODE_HEIGHT}
                    rx={8}
                    fill={dimmed ? "#141a2c" : kind === "inline_subflow" ? "#15261a" : "#1b2238"}
                    stroke={strokeColor}
                    strokeWidth={isFocus ? 3 : onPath ? 2.5 : 2}
                    strokeDasharray={kind === "inline_subflow" ? "6 4" : undefined}
                    opacity={dimmed ? 0.35 : 1}
                  />
                  <text
                    x={node.x + 10}
                    y={node.y + 18}
                    fill={dimmed ? "#6f7d9c" : "#e9eefc"}
                    fontSize={12}
                  >
                    {truncate(nodeKindPrefix(kind) + node.label, 22)}
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
      {expandedEntries.length > 0 ? (
        <div className="dag-inline-expansions">
          {expandedEntries.map(([nodeId, state]) => {
            const meta = dag.nodes.find((node) => node.id === nodeId);
            return (
              <section key={nodeId} className="dag-inline-expansion">
                <h4>
                  Inline subflow: {meta?.label ?? nodeId}
                  {meta?.child_flow_run_id ? (
                    <button
                      type="button"
                      className="dag-ghost-btn dag-inline-open-btn"
                      onClick={() => navigate(`/runs/${meta.child_flow_run_id}`)}
                    >
                      Open run
                    </button>
                  ) : null}
                </h4>
                {state.loading ? <p>Loading inline subflow DAG…</p> : null}
                {state.error ? <p className="dag-warning">{state.error}</p> : null}
                {state.dag ? <InlineSubflowMiniDag dag={state.dag} /> : null}
              </section>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

function InlineSubflowMiniDag({ dag }: { dag: FlowRunDag }) {
  const layout = useMemo(() => layoutDag(dag.nodes, dag.edges), [dag.nodes, dag.edges]);
  const byId = useMemo(() => new Map(layout.nodes.map((node) => [node.id, node])), [layout.nodes]);
  const width = Math.max(480, layout.bounds.maxX + 24);
  const height = Math.max(160, layout.bounds.maxY + 24);

  return (
    <svg width={width} height={height} className="dag-svg dag-inline-mini">
      {dag.edges.map((edge, idx) => {
        const from = byId.get(edge.from);
        const to = byId.get(edge.to);
        if (!from || !to) return null;
        const { x1, y1, x2, y2 } = edgeEndpoints(from, to, layout.orientation, NODE_WIDTH, NODE_HEIGHT);
        return (
          <line
            key={`${edge.from}-${edge.to}-${idx}`}
            x1={x1}
            y1={y1}
            x2={x2}
            y2={y2}
            stroke="#556082"
            strokeWidth={1.5}
          />
        );
      })}
      {layout.nodes.map((node) => (
        <g key={node.id}>
          <rect
            x={node.x}
            y={node.y}
            width={NODE_WIDTH}
            height={NODE_HEIGHT}
            rx={8}
            fill="#1b2238"
            stroke={stateColor[node.state] ?? "#556082"}
            strokeWidth={1.5}
          />
          <text x={node.x + 10} y={node.y + 18} fill="#e9eefc" fontSize={11}>
            {truncate(node.label, 20)}
          </text>
        </g>
      ))}
    </svg>
  );
}

function nodeKindPrefix(kind: DagNodeKind) {
  if (kind === "inline_subflow") return "⧉ ";
  if (kind === "subflow_task") return "↗ ";
  return "";
}

type DagNodeKind = "task" | "inline_subflow" | "subflow_task";

function truncate(value: string, max: number) {
  return value.length <= max ? value : `${value.slice(0, max - 1)}…`;
}
