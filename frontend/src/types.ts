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
