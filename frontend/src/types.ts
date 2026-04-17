export type CursorPage<T> = {
  items: T[];
  next_cursor: string | null;
};

export type FlowRun = {
  id: string;
  name: string;
  state: string;
  version: number;
  created_at: string;
  updated_at: string;
};

export type TaskRun = {
  id: string;
  flow_run_id: string;
  task_name: string;
  planned_node_id?: string | null;
  state: string;
  version: number;
  created_at: string;
  updated_at: string;
};

export type LogRecord = {
  id: string;
  flow_run_id: string;
  task_run_id?: string | null;
  level: string;
  message: string;
  timestamp: string;
};

export type EventRecord = {
  event_id: string;
  run_id: string;
  task_run_id?: string | null;
  from_state?: string | null;
  to_state?: string | null;
  event_type?: string | null;
  kind?: string | null;
  data: Record<string, unknown>;
  timestamp: string;
};

export type ArtifactRecord = {
  id: string;
  flow_run_id: string;
  task_run_id?: string | null;
  artifact_type: string;
  key: string;
  summary?: string | null;
  created_at: string;
};

export type DagNode = {
  id: string;
  label: string;
  task_name?: string;
  op_type?: string;
  planned_node_id?: string | null;
  state: string;
};

export type DagEdge = {
  from: string;
  to: string;
};

export type FlowRunDag = {
  flow_run_id: string;
  mode: "logical" | "expanded";
  source: string;
  fallback_required: boolean;
  warnings: string[];
  forecast: Record<string, unknown>;
  nodes: DagNode[];
  edges: DagEdge[];
};

export type Deployment = {
  id: string;
  name: string;
  flow_name: string;
  entrypoint?: string | null;
  path?: string | null;
  default_parameters: Record<string, unknown>;
  paused: boolean;
  created_at: string;
  updated_at: string;
};

export type DeploymentRun = {
  id: string;
  deployment_id: string;
  status: string;
  requested_parameters: Record<string, unknown>;
  resolved_parameters: Record<string, unknown>;
  idempotency_key?: string | null;
  worker_name?: string | null;
  lease_until?: string | null;
  flow_run_id?: string | null;
  error?: string | null;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  finished_at?: string | null;
};
