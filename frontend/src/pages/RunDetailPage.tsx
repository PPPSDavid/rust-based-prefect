import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useEffect } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { RunDagPanel } from "../components/RunDagPanel";
import { useSsePulse } from "../hooks/useSsePulse";
import type { FlowRunDag } from "../types";

type Tab = "tasks" | "logs" | "events" | "artifacts" | "dag";

export function RunDetailPage() {
  const { id = "" } = useParams();
  const [tab, setTab] = useState<Tab>("tasks");
  const [dagMode, setDagMode] = useState<"logical" | "expanded">("logical");
  const queryClient = useQueryClient();
  const pulse = useSsePulse(useMemo(() => () => api.streamFlowRun(id), [id]));

  useEffect(() => {
    if (pulse > 0) {
      void queryClient.invalidateQueries({ queryKey: ["flow-run", id] });
      void queryClient.invalidateQueries({ queryKey: ["task-runs", id] });
      void queryClient.invalidateQueries({ queryKey: ["logs", id] });
      void queryClient.invalidateQueries({ queryKey: ["events", id] });
      void queryClient.invalidateQueries({ queryKey: ["artifacts", id] });
      void queryClient.invalidateQueries({ queryKey: ["dag", id, dagMode] });
    }
  }, [pulse, id, dagMode, queryClient]);

  const run = useQuery({ queryKey: ["flow-run", id], queryFn: () => api.getFlowRun(id), staleTime: 5_000 });
  const tasks = useQuery({ queryKey: ["task-runs", id], queryFn: () => api.listTaskRuns(id), staleTime: 5_000 });
  const logs = useQuery({ queryKey: ["logs", id], queryFn: () => api.listLogs(id), staleTime: 5_000 });
  const events = useQuery({ queryKey: ["events", id], queryFn: () => api.listEvents(id), staleTime: 5_000 });
  const artifacts = useQuery({
    queryKey: ["artifacts", id],
    queryFn: () => api.listFlowArtifacts(id),
    staleTime: 5_000
  });
  const dag = useQuery({
    queryKey: ["dag", id, dagMode],
    queryFn: () => api.getFlowRunDag(id, dagMode),
    staleTime: 5_000
  });

  useEffect(() => {
    if (pulse <= 0 || !tasks.data) return;
    queryClient.setQueryData<FlowRunDag | undefined>(["dag", id, dagMode], (current) => {
      if (!current) return current;
      const nextNodes = current.nodes.map((node) => {
        const related = tasks.data.items.filter(
          (task) => task.planned_node_id === node.id || task.task_name === node.task_name
        );
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

  return (
    <section>
      <h2>{run.data.name}</h2>
      <p>
        <b>{run.data.state}</b> · version {run.data.version}
      </p>
      <div className="tabs">
        <button onClick={() => setTab("tasks")}>Task Runs</button>
        <button onClick={() => setTab("logs")}>Logs</button>
        <button onClick={() => setTab("events")}>Events</button>
        <button onClick={() => setTab("artifacts")}>Artifacts</button>
        <button onClick={() => setTab("dag")}>DAG</button>
      </div>
      {tab === "tasks" && (
        <ul>
          {tasks.data?.items.map((task) => (
            <li key={task.id}>
              {task.task_name} - {task.state}
            </li>
          ))}
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
              {event.timestamp} {event.event_type ?? event.kind} {event.from_state ?? ""}{" "}
              {event.to_state ?? ""}
            </li>
          ))}
        </ul>
      )}
      {tab === "artifacts" && (
        <ul className="mono-list">
          {artifacts.data?.map((artifact) => (
            <li key={artifact.id}>
              {artifact.key} ({artifact.artifact_type}) {artifact.summary ?? ""}
            </li>
          ))}
        </ul>
      )}
      {tab === "dag" && dag.data && (
        <RunDagPanel dag={dag.data} mode={dagMode} onModeChange={setDagMode} />
      )}
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
