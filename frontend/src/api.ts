import type {
  ArtifactRecord,
  ConcurrencyLimit,
  CursorPage,
  Deployment,
  DeploymentRun,
  EventRecord,
  FlowDetail,
  FlowRun,
  FlowRunDag,
  LogRecord,
  TaskRun,
  WorkPool,
  Worker
} from "./types";

const base = import.meta.env.VITE_API_BASE ?? "";

async function readJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Request failed: ${res.status}`);
  }
  return (await res.json()) as T;
}

function pageUrl(path: string, params: Record<string, string | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) search.set(key, value);
  }
  const qs = search.toString();
  return `${base}${path}${qs ? `?${qs}` : ""}`;
}

export const api = {
  listFlowRuns: (cursor?: string, state?: string) =>
    readJson<CursorPage<FlowRun>>(pageUrl("/api/flow-runs", { limit: "50", cursor, state })),
  getFlowRun: (id: string) => readJson<FlowRun>(`${base}/api/flow-runs/${id}`),
  cancelFlowRun: (id: string) =>
    readJson<FlowRun>(`${base}/api/flow-runs/${id}/cancel`, { method: "POST" }),
  pauseFlowRun: (id: string, mode: "drain" | "terminate") =>
    readJson<FlowRun>(`${base}/api/flow-runs/${id}/pause`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode })
    }),
  resumeFlowRun: (id: string) =>
    readJson<FlowRun>(`${base}/api/flow-runs/${id}/resume`, { method: "POST" }),
  retryFlowRun: (id: string) =>
    readJson<DeploymentRun>(`${base}/api/flow-runs/${id}/retry`, { method: "POST" }),
  listTaskRuns: (id: string) =>
    readJson<CursorPage<TaskRun>>(`${base}/api/flow-runs/${id}/task-runs?limit=500`),
  listLogs: (id: string, params?: { task_run_id?: string; level?: string }) =>
    readJson<CursorPage<LogRecord>>(
      pageUrl(`/api/flow-runs/${id}/logs`, {
        limit: "1000",
        task_run_id: params?.task_run_id,
        level: params?.level
      })
    ),
  listFlows: (cursor?: string) =>
    readJson<CursorPage<{ name: string; run_count: number; updated_at: string }>>(
      pageUrl("/api/flows", { limit: "200", cursor })
    ),
  getFlow: (flowName: string) => readJson<FlowDetail>(`${base}/api/flows/${encodeURIComponent(flowName)}`),
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
  listDeployments: (cursor?: string) =>
    readJson<CursorPage<Deployment>>(pageUrl("/api/deployments", { limit: "200", cursor })),
  getDeployment: (id: string) => readJson<Deployment>(`${base}/api/deployments/${id}`),
  createDeployment: (payload: Partial<Deployment> & { name: string; flow_name: string }) =>
    readJson<Deployment>(`${base}/api/deployments`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  patchDeployment: (id: string, payload: Partial<Deployment>) =>
    readJson<Deployment>(`${base}/api/deployments/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  listDeploymentRuns: (deploymentId?: string, cursor?: string) =>
    readJson<CursorPage<DeploymentRun>>(
      pageUrl("/api/deployment-runs", {
        limit: "50",
        deployment_id: deploymentId,
        cursor
      })
    ),
  triggerDeploymentRun: (
    deploymentId: string,
    payload?: { parameters?: Record<string, unknown>; idempotency_key?: string }
  ) =>
    readJson<DeploymentRun>(`${base}/api/deployments/${deploymentId}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload ?? {})
    }),
  listWorkPools: (cursor?: string) =>
    readJson<CursorPage<WorkPool>>(pageUrl("/api/work-pools", { limit: "50", cursor })),
  getWorkPool: (id: string) => readJson<WorkPool>(`${base}/api/work-pools/${id}`),
  createWorkPool: (payload: { name: string; type?: string }) =>
    readJson<WorkPool>(`${base}/api/work-pools`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  patchWorkPool: (id: string, payload: { paused?: boolean }) =>
    readJson<WorkPool>(`${base}/api/work-pools/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  listWorkers: (workPoolId?: string) =>
    readJson<CursorPage<Worker>>(
      pageUrl("/api/workers", { limit: "100", work_pool_id: workPoolId })
    ),
  workerHeartbeat: (name: string, workPoolId?: string) =>
    readJson<Worker>(`${base}/api/workers/heartbeat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, work_pool_id: workPoolId })
    }),
  listConcurrencyLimits: () =>
    readJson<{ limits: ConcurrencyLimit[] }>(`${base}/api/concurrency-limits`),
  getConcurrencyLimit: (name: string) =>
    readJson<ConcurrencyLimit>(`${base}/api/concurrency-limits/${encodeURIComponent(name)}`),
  upsertConcurrencyLimit: (payload: {
    name: string;
    limit: number;
    slot_decay_per_second?: number;
    active?: boolean;
  }) =>
    readJson<ConcurrencyLimit>(`${base}/api/concurrency-limits`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  patchConcurrencyLimit: (
    name: string,
    payload: { limit?: number; slot_decay_per_second?: number; active?: boolean }
  ) =>
    readJson<ConcurrencyLimit>(`${base}/api/concurrency-limits/${encodeURIComponent(name)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  deleteConcurrencyLimit: (name: string) =>
    readJson<{ ok?: boolean; deleted?: boolean }>(
      `${base}/api/concurrency-limits/${encodeURIComponent(name)}`,
      { method: "DELETE" }
    ),
  streamFlowRuns: () => new EventSource(`${base}/api/stream/flow-runs`),
  streamFlowRun: (id: string) => new EventSource(`${base}/api/stream/flow-runs/${id}`)
};
