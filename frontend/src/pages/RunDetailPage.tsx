import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { useSsePulse } from "../hooks/useSsePulse";

type Tab = "tasks" | "logs" | "events" | "artifacts";

export function RunDetailPage() {
  const { id = "" } = useParams();
  const [tab, setTab] = useState<Tab>("tasks");
  const pulse = useSsePulse(useMemo(() => () => api.streamFlowRun(id), [id]));

  const run = useQuery({ queryKey: ["flow-run", id, pulse], queryFn: () => api.getFlowRun(id) });
  const tasks = useQuery({ queryKey: ["task-runs", id, pulse], queryFn: () => api.listTaskRuns(id) });
  const logs = useQuery({ queryKey: ["logs", id, pulse], queryFn: () => api.listLogs(id) });
  const events = useQuery({ queryKey: ["events", id, pulse], queryFn: () => api.listEvents(id) });
  const artifacts = useQuery({
    queryKey: ["artifacts", id, pulse],
    queryFn: () => api.listFlowArtifacts(id)
  });

  if (run.isLoading) return <p>Loading run...</p>;
  if (run.error || !run.data) return <p>Unable to load run.</p>;

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
    </section>
  );
}
