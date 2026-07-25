import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { formatTaskResult, parseTaskResultSummary } from "../artifactResult";
import { ActionButton } from "../components/ActionButton";
import { PageHeader } from "../components/PageHeader";
import { RunDagPanel } from "../components/RunDagPanel";
import { StateBadge } from "../components/StateBadge";
import { TabBar } from "../components/TabBar";
import { useSsePulse } from "../hooks/useSsePulse";
import type { ArtifactRecord, FlowRunDag } from "../types";

type Tab = "tasks" | "logs" | "events" | "artifacts" | "dag";

const TABS = [
  { id: "tasks" as const, label: "Task Runs" },
  { id: "logs" as const, label: "Logs" },
  { id: "events" as const, label: "Events" },
  { id: "artifacts" as const, label: "Artifacts" },
  { id: "dag" as const, label: "DAG" }
];

const CANCELLABLE = new Set(["SCHEDULED", "PENDING", "RUNNING"]);
const RETRYABLE = new Set(["FAILED", "CANCELLED"]);

export function RunDetailPage() {
  const { id = "" } = useParams();
  const [searchParams] = useSearchParams();
  const initialTab = (searchParams.get("tab") as Tab | null) ?? "tasks";
  const [tab, setTab] = useState<Tab>(initialTab);
  const [dagMode, setDagMode] = useState<"logical" | "expanded">("logical");
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const pulse = useSsePulse(useMemo(() => () => api.streamFlowRun(id), [id]));

  useEffect(() => {
    if (pulse > 0) {
      void queryClient.invalidateQueries({ queryKey: ["flow-run", id] });
      void queryClient.invalidateQueries({ queryKey: ["task-runs", id] });
      if (tab === "logs") void queryClient.invalidateQueries({ queryKey: ["logs", id] });
      if (tab === "events") void queryClient.invalidateQueries({ queryKey: ["events", id] });
      if (tab === "artifacts" || tab === "tasks") {
        void queryClient.invalidateQueries({ queryKey: ["artifacts", id] });
      }
      if (tab === "dag") void queryClient.invalidateQueries({ queryKey: ["dag", id, dagMode] });
    }
  }, [pulse, id, dagMode, queryClient, tab]);

  const run = useQuery({ queryKey: ["flow-run", id], queryFn: () => api.getFlowRun(id), staleTime: 5_000 });
  const tasks = useQuery({
    queryKey: ["task-runs", id],
    queryFn: () => api.listTaskRuns(id),
    staleTime: 5_000,
    enabled: tab === "tasks" || tab === "dag"
  });
  const logs = useQuery({
    queryKey: ["logs", id],
    queryFn: () => api.listLogs(id),
    staleTime: 5_000,
    enabled: tab === "logs"
  });
  const events = useQuery({
    queryKey: ["events", id],
    queryFn: () => api.listEvents(id),
    staleTime: 5_000,
    enabled: tab === "events"
  });
  const artifacts = useQuery({
    queryKey: ["artifacts", id],
    queryFn: () => api.listFlowArtifacts(id),
    staleTime: 5_000,
    enabled: tab === "artifacts" || tab === "tasks"
  });
  const resultByTaskId = useMemo(() => {
    const map = new Map<string, ArtifactRecord>();
    for (const artifact of artifacts.data ?? []) {
      if (artifact.artifact_type === "result" && artifact.task_run_id && !map.has(artifact.task_run_id)) {
        map.set(artifact.task_run_id, artifact);
      }
    }
    return map;
  }, [artifacts.data]);
  const dag = useQuery({
    queryKey: ["dag", id, dagMode],
    queryFn: () => api.getFlowRunDag(id, dagMode),
    staleTime: 5_000,
    enabled: tab === "dag"
  });

  const cancelRun = useMutation({
    mutationFn: () => api.cancelFlowRun(id),
    onSuccess: () => {
      setActionMessage("Run cancelled.");
      void queryClient.invalidateQueries({ queryKey: ["flow-run", id] });
    },
    onError: () => setActionMessage("Failed to cancel run.")
  });
  const retryRun = useMutation({
    mutationFn: () => api.retryFlowRun(id),
    onSuccess: () => {
      setActionMessage("Retry scheduled from deployment.");
      void queryClient.invalidateQueries({ queryKey: ["flow-runs"] });
    },
    onError: () => setActionMessage("Retry is only available for deployment-backed runs.")
  });

  useEffect(() => {
    if (pulse <= 0 || !tasks.data) return;
    queryClient.setQueryData<FlowRunDag | undefined>(["dag", id, dagMode], (current) => {
      if (!current) return current;
      const nextNodes = current.nodes.map((node) => {
        const related = tasks.data.items.filter((task) => task.planned_node_id === node.id);
        if (related.length === 0) return node;
        const states = related.map((t) => t.state);
        const nextState = aggregateState(states);
        return { ...node, state: nextState };
      });
      return { ...current, nodes: nextNodes };
    });
  }, [pulse, tasks.data, dagMode, id, queryClient]);

  if (run.isLoading && !run.data) return <p>Loading run...</p>;
  if ((run.error && !run.data) || !run.data) return <p>Unable to load run.</p>;

  const breadcrumbItems =
    run.data.breadcrumb && run.data.breadcrumb.length > 0
      ? [
          { label: "Flow Runs", to: "/runs" },
          ...run.data.breadcrumb.map((crumb, index) => ({
            label:
              crumb.execution_mode === "inline" && index < run.data!.breadcrumb!.length - 1
                ? `${crumb.name} (inline)`
                : crumb.name,
            to: index < run.data!.breadcrumb!.length - 1 ? `/runs/${crumb.id}` : undefined
          }))
        ]
      : [
          { label: "Flow Runs", to: "/runs" },
          { label: run.data.name }
        ];

  const children = run.data.children_summary;
  const childRuns = run.data.children ?? [];
  const hasChildren =
    children &&
    (children.inline_subflows > 0 || children.subflow_tasks > 0 || children.deployment_subflows > 0);
  const parentCrumb =
    run.data.breadcrumb && run.data.breadcrumb.length > 1
      ? run.data.breadcrumb[run.data.breadcrumb.length - 2]
      : undefined;

  return (
    <section>
      <PageHeader
        title={run.data.name}
        subtitle={`Run ${run.data.id}`}
        breadcrumbs={breadcrumbItems}
        actions={
          <>
            {CANCELLABLE.has(run.data.state) ? (
              <ActionButton variant="danger" disabled={cancelRun.isPending} onClick={() => cancelRun.mutate()}>
                Cancel
              </ActionButton>
            ) : null}
            {RETRYABLE.has(run.data.state) ? (
              <ActionButton variant="primary" disabled={retryRun.isPending} onClick={() => retryRun.mutate()}>
                Retry
              </ActionButton>
            ) : null}
          </>
        }
      />
      <p>
        <StateBadge state={run.data.state} /> · version {run.data.version} · updated{" "}
        {new Date(run.data.updated_at).toLocaleString()}
        {run.data.execution_mode ? ` · ${run.data.execution_mode} subflow` : ""}
        {typeof run.data.depth === "number" && run.data.depth > 0 ? ` · depth ${run.data.depth}` : ""}
      </p>
      {run.data.parent_flow_run_id ? (
        <p>
          Parent run:{" "}
          <Link to={`/runs/${run.data.parent_flow_run_id}`}>
            {parentCrumb?.name ?? run.data.parent_flow_run_id.slice(0, 8) + "…"}
          </Link>
          {parentCrumb?.execution_mode ? ` (${parentCrumb.execution_mode})` : ""}
        </p>
      ) : null}
      {hasChildren ? (
        <p className="run-children-summary">
          Subflows: {children!.inline_subflows} inline · {children!.subflow_tasks} deployment task
          {children!.deployment_subflows > 0 ? ` · ${children!.deployment_subflows} deployment child` : ""}
        </p>
      ) : null}
      {childRuns.length > 0 ? (
        <section className="run-children-list" aria-label="Child flow runs">
          <h3>Child flow runs</h3>
          <ul>
            {childRuns.map((child) => (
              <li key={child.id}>
                <Link to={`/runs/${child.id}`}>{child.name}</Link>{" "}
                <StateBadge state={child.state} />
                {child.execution_mode ? ` · ${child.execution_mode}` : ""}
                <span className="mono"> · {child.id}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {run.data.deployment_id ? (
        <p>
          Deployment: <Link to={`/deployments/${run.data.deployment_id}`}>{run.data.deployment_id.slice(0, 8)}</Link>
        </p>
      ) : null}
      {actionMessage ? <p className="action-message">{actionMessage}</p> : null}
      <TabBar tabs={TABS} activeTab={tab} onChange={setTab} />
      {tab === "tasks" && (
        <ul>
          {tasks.data?.items.map((task) => {
            const artifact = resultByTaskId.get(task.id);
            const parsed = parseTaskResultSummary(artifact?.summary);
            return (
              <li key={task.id}>
                {task.task_name} - {task.state}
                {parsed.cacheHit ? " · resumed" : null}
                {task.kind === "subflow" && task.child_flow_run_id ? (
                  <>
                    {" "}
                    ·{" "}
                    <Link to={`/runs/${task.child_flow_run_id}`}>child run {task.child_flow_run_id.slice(0, 8)}…</Link>
                  </>
                ) : null}
                {parsed.hasResult ? (
                  <pre className="task-result mono-list">{formatTaskResult(parsed.result)}</pre>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      {tab === "logs" && (
        <ul className="mono-list">
          {logs.data?.items.map((log) => (
            <li key={log.id}>
              [{log.level}] {log.message}
            </li>
          ))}
        </ul>
      )}
      {tab === "events" && (
        <ul className="mono-list">
          {events.data?.items.map((event) => (
            <li key={event.event_id}>
              {event.timestamp} {event.event_type ?? event.kind} {event.from_state ?? ""} {event.to_state ?? ""}
            </li>
          ))}
        </ul>
      )}
      {tab === "artifacts" && (
        <ul className="mono-list">
          {artifacts.data?.map((artifact) => {
            const parsed = parseTaskResultSummary(artifact.summary);
            return (
              <li key={artifact.id}>
                {artifact.key} ({artifact.artifact_type})
                {parsed.hasResult ? (
                  <pre className="task-result">{formatTaskResult(parsed.result)}</pre>
                ) : (
                  <> {artifact.summary ?? ""}</>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {tab === "dag" && dag.data && <RunDagPanel dag={dag.data} mode={dagMode} onModeChange={setDagMode} />}
      {tab === "dag" && dag.isLoading && <p>Loading DAG...</p>}
    </section>
  );
}

function aggregateState(states: string[]): string {
  const priority = ["FAILED", "CANCELLED", "RUNNING", "PENDING", "SCHEDULED", "COMPLETED"];
  for (const state of priority) {
    if (states.includes(state)) return state;
  }
  return states[0] ?? "PENDING";
}
