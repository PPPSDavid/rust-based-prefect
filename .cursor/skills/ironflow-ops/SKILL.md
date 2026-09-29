---
name: ironflow-ops
description: Inspect and troubleshoot IronFlow deployments and flow runs via the read-only IronFlow MCP server. Use for IronFlow operational questions, failure diagnosis, and DAG/forecast inspection. Mutations use the ironflow CLI skill path instead.
---

# IronFlow operations

Prefer **IronFlow MCP tools** for reads (structured JSON, full UUIDs). They are
read-only.

## Diagnose

1. `get_dashboard` — recent runs, work pools, concurrency limits.
2. `get_flow_runs` / `get_flow_run` — preserve full UUIDs.
3. `get_flow_run_logs`, `read_events`, `get_task_runs`, `get_flow_run_dag`
   for the failing layer.
4. Explain evidence; recommend the smallest next check.

Do not re-fetch broad lists when an id is already known.

## Mutations

MCP does **not** mutate. Use the CLI (shell access required):

```bash
ironflow deployment run 'my-deployment' --param n=1
ironflow flow-run cancel <uuid>
ironflow flow-run pause <uuid> --mode drain   # or terminate
ironflow flow-run resume <uuid>
ironflow flow-run retry <uuid>
ironflow api GET /api/flow-runs --query state=FAILED
```

Guide: `docs/how-to/ai-assistants.md`.
