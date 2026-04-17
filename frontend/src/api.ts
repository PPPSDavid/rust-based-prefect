import type {
  ArtifactRecord,
  CursorPage,
  EventRecord,
  FlowRun,
  FlowRunDag,
  LogRecord,
  TaskRun,
  Deployment,
  DeploymentRun
} from "./types";

const base = "http://127.0.0.1:8000";

async function readJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return (await res.json()) as T;
}

export const api = {
  listFlowRuns: (cursor?: string, state?: string) =>
    readJson<CursorPage<FlowRun>>(
      `${base}/api/flow-runs?limit=50${cursor ? `&cursor=${cursor}` : ""}${state ? `&state=${state}` : ""}`
    ),
  getFlowRun: (id: string) => readJson<FlowRun>(`${base}/api/flow-runs/${id}`),
  listTaskRuns: (id: string) =>
    readJson<CursorPage<TaskRun>>(`${base}/api/flow-runs/${id}/task-runs?limit=500`),
  listLogs: (id: string) => readJson<CursorPage<LogRecord>>(`${base}/api/flow-runs/${id}/logs?limit=1000`),
  listFlows: () => readJson<CursorPage<{ name: string; run_count: number; updated_at: string }>>(`${base}/api/flows`),
  listTasks: (flowName?: string) =>
    readJson<Array<{ task_name: string; run_count: number; updated_at: string }>>(
      `${base}/api/tasks${flowName ? `?flow_name=${encodeURIComponent(flowName)}` : ""}`
    ),
  listEvents: (id: string) =>
    readJson<CursorPage<EventRecord>>(`${base}/api/flow-runs/${id}/events?limit=1000`),
  getFlowRunDag: (id: string, mode: "logical" | "expanded") =>
    readJson<FlowRunDag>(`${base}/api/flow-runs/${id}/dag?mode=${mode}`),
  listFlowArtifacts: (id: string) =>
    readJson<ArtifactRecord[]>(`${base}/api/flow-runs/${id}/artifacts`),
  listDeployments: () => readJson<CursorPage<Deployment>>(`${base}/api/deployments?limit=200`),
  triggerDeploymentRun: (deploymentId: string, payload?: { parameters?: Record<string, unknown>; idempotency_key?: string }) =>
    fetch(`${base}/api/deployments/${deploymentId}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload ?? {})
    }).then(async (res) => {
      if (!res.ok) {
        throw new Error(`Request failed: ${res.status}`);
      }
      return (await res.json()) as DeploymentRun;
    }),
  streamFlowRuns: () => new EventSource(`${base}/api/stream/flow-runs`),
  streamFlowRun: (id: string) => new EventSource(`${base}/api/stream/flow-runs/${id}`)
};
