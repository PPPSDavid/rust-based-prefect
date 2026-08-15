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
  deployment_id?: string | null;
  parent_flow_run_id?: string | null;
  root_flow_run_id?: string | null;
  execution_mode?: string | null;
  depth?: number;
  breadcrumb?: FlowRunBreadcrumb[];
  children_summary?: FlowRunChildrenSummary;
  children?: FlowRunChild[];
  lifecycle_action?: string | null;
  interrupt_mode?: string | null;
  lifecycle_summary?: string | null;
  pause_drain_pending?: boolean;
  parameters?: Record<string, unknown> | null;
  resume_from_flow_run_id?: string | null;
  resume_lineage_id?: string | null;
};

export type FlowRunChild = {
  id: string;
  name: string;
  state: string;
  execution_mode?: string | null;
  depth?: number;
  created_at: string;
  updated_at: string;
};

export type FlowRunBreadcrumb = {
  id: string;
  name: string;
  execution_mode?: string | null;
};

export type FlowRunChildrenSummary = {
  inline_subflows: number;
  subflow_tasks: number;
  deployment_subflows: number;
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
  kind?: string;
  child_flow_run_id?: string | null;
  child_deployment_run_id?: string | null;
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
  kind?: "task" | "inline_subflow" | "subflow_task" | "gate_task";
  child_flow_run_id?: string | null;
  child_deployment_run_id?: string | null;
  gate_open_at?: string | null;
  execution_mode?: string | null;
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
  concurrency_limit?: number | null;
  collision_strategy?: string;
  schedule_interval_seconds?: number | null;
  schedule_cron?: string | null;
  schedule_rrule?: string | null;
  schedule_next_run_at?: string | null;
  schedule_enabled?: boolean;
  work_pool_id?: string | null;
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

export type FlowDetail = {
  name: string;
  tasks: Array<{ task_name: string; run_count: number; updated_at: string }>;
};

export type WorkPool = {
  id: string;
  name: string;
  type: string;
  paused: boolean;
  created_at: string;
  updated_at: string;
};

export type Worker = {
  name: string;
  status: string;
  last_heartbeat: string;
  updated_at: string;
  work_pool_id?: string | null;
};
