# Agent-native IronFlow roadmap

**Status:** Living plan — **Phase 0 in progress / shipping**  
**Date:** 2026-09-07  
**Audience:** Maintainers choosing agent-facing surfaces (CLI, MCP, skills, docs)  
**Related:** [`prefect-gap-canvas.md`](prefect-gap-canvas.md), Prefect
[`prefect-mcp-server`](https://github.com/PrefectHQ/prefect-mcp-server),
[AI assistants how-to](https://docs.prefect.io/v3/how-to-guides/ai/use-prefect-mcp-server)

IronFlow already has a strong HTTP control plane. What it lacked was an
**agent-native narrative**: structured tools and JSON-first commands so coding
agents and agentic systems can operate the scheduler without scraping the UI.

---

## 1. What Prefect shipped (research summary)

| Surface | What it is | Relevance to IronFlow |
| --- | --- | --- |
| **`prefect-mcp-server` (beta)** | Read-only MCP tools: dashboard, flows, deployments, flow runs, logs, task runs, work pools, events, automations, object schema; mounted **docs proxy** | Borrow tool vocabulary + read-only default |
| **Plugin + skills** | Claude Code / Codex marketplace plugin; `workflows` skill for diagnose; mutations via CLI skill | Same packaging shape for Phase 1 |
| **CLI for writes** | MCP never mutates; agents use `prefect deployment run`, `--no-prompt`, `-o json`, `prefect api METHOD path` | Phase 0 mirrors this: `ironflow api` + JSON `flow-run` / `deployment` |
| **Execution plans (Cloud, experimental)** | Agents author a DAG document: schema → validate → publish → get | Closest to our static planner; we can do it **locally with forecast** |
| **FastMCP 4** | Sessionless protocol, `@mcp.tool(task=True)` background tasks, identity assertion | Chosen SDK for `ironflow-mcp` |
| **Horizon** | Hosted MCP deploy / registry / gateway | Park — Cloud enterprise, not our self-hosted MVP |
| **Durable agents** | Pydantic AI `PrefectDurability`: agent run = flow, model/tool calls = tasks | Phase 4 recipe using existing `@task` retries + resume |

Prefect’s security posture is deliberate: **MCP tools are read-only**; mutations
go through a separately authenticated CLI. We keep the same default.

---

## 2. Where IronFlow stands today

| Surface | Status |
| --- | --- |
| HTTP `/api/*` + OpenAPI + optional Basic auth | Strong |
| CLI `init` / `deploy` / `serve` / `worker` / `flow` / `gcl` | Partial — no `flow-run`, `deployment`, or raw `api` (Phase 0) |
| Static planner | Library only — no CLI / MCP (Phase 2) |
| `llms.txt` via `docs/gen_llms.py` | Exists |
| IronFlow MCP server | Missing (Phase 1) |
| Automations / webhooks | Gap (see gap canvas P7) |

Nothing in this roadmap is a Rust hot path. Agent surfaces stay thin Python
bridges over the existing HTTP API and `static_planner`.

```text
Agent clients (Claude Code, Cursor, Codex, custom)
  ├─ ironflow CLI (JSON)     ← Phase 0
  ├─ ironflow-mcp (FastMCP)  ← Phases 1–3
  └─ llms.txt + skills       ← Phase 0–1
        │
        ▼
IronFlow HTTP /api/*  →  Rust engine + persistence
static_planner        →  Phase 2 MCP/CLI exposure
```

---

## 3. Decisions (locked)

| Decision | Choice | Why |
| --- | --- | --- |
| MCP SDK | **FastMCP 4** | Protocol + background tasks; matches Prefect’s stack agents already know |
| Package layout | Separate uv workspace member **`ironflow-mcp/`** | Keep `fastmcp` out of core `ironflow-prefect-compat` deps |
| Mutation posture | **Read-only MCP by default**; writes behind `IRONFLOW_MCP_ALLOW_WRITES=1` (Phase 3) | Same safety model as Prefect |
| Differentiator | Expose **static planner + forecast** as agent tools (Phase 2) | Prefect execution plans are Cloud-only and lack forecast |

---

## 4. Phases (ordered by bang for the buck)

### Phase 0 — Agent-legible CLI + docs (this train)

**Gets you:** any shell-capable agent can operate IronFlow today.

| Deliverable | Acceptance |
| --- | --- |
| `ironflow api METHOD PATH [--data] [--query]` | Raw JSON passthrough over `/api/*` (and OpenAPI) with auth env |
| `ironflow flow-run …` | `ls` / `inspect` / `task-runs` / `logs` / `events` / `dag` / `cancel` / `pause --mode` / `resume` / `retry` → JSON |
| `ironflow deployment …` | `ls` / `inspect` / `run` → JSON; `run` returns immediately (handle pattern) |
| `docs/how-to/ai-assistants.md` | Cheat sheet + safety notes; registered in mkdocs / `llms.txt` |
| COMPATIBILITY | CLI pause helpers closed; CLI row lists new commands |

**Ownership:** `python-shim` CLI + docs. **No new deps.**

### Phase 1 — Read-only `ironflow-mcp` + plugin

**Gets you:** “IronFlow has an MCP server” with Prefect-compatible tool names.

- Package `ironflow-mcp/` with console script `ironflow-mcp` (stdio default).
- Tools: `orientation`, `get_server_info`, `get_dashboard`, `get_flows`,
  `get_deployments`, `get_flow_runs`, `get_flow_run`, `get_task_runs`,
  `get_flow_run_logs`, `read_events`, `get_work_pools`,
  `get_concurrency_limits`, `get_object_schema`, plus differentiator
  `get_flow_run_dag`. All `readOnlyHint=True`.
- Resource `ironflow://docs/llms.txt`.
- Plugin: Claude Code / Codex marketplace skeleton + `skills/workflows` +
  `skills/cli` (mutations → Phase 0 CLI). Cursor `mcp.json` snippet in docs.
- Tests: FastMCP in-memory client + scripted scenario evals (no LLM).

### Phase 2 — Static planner as an agent tool

**Gets you:** compile → validate → forecast **before** deploy (OSS, local).

- MCP: `plan_compile`, `plan_validate`; resource `ironflow://planner/schema`.
- CLI: `ironflow plan compile <file> [--flow]`.
- Thin over `static_planner.compile_and_forecast`.

### Phase 3 — Opt-in writes + wait / background

**Gets you:** full agent loop without shell access; safe defaults preserved.

- Gate: `IRONFLOW_MCP_ALLOW_WRITES=1` (structured disabled response otherwise).
- Tools: `run_deployment`, `cancel_flow_run`, `pause_flow_run`, `resume_flow_run`,
  `retry_flow_run`, `apply_manifest` (`destructiveHint=True`).
- `wait_for_flow_run`; optional FastMCP `task=True` background run.
- CLI: `ironflow deployment run --wait`.

### Phase 4 — Durable-agent recipe

**Gets you:** second Prefect narrative with existing primitives.

- Example + how-to: model/tool steps as `@task(retries=…, persist_result=True)`,
  resume-on-retry, `on_transition` hooks.
- Pydantic AI wrapper **parked** unless demand.

### Phase 5 — Park / later

- Hosted HTTP + OAuth / identity assertion
- Horizon-style gateway / registry
- Automations / webhooks (gap canvas P7)
- AI log summaries; LLM-driven evals

---

## 5. Session queue

1. **Phase 0** — this PR / train  
2. Phase 1 — `ironflow-mcp` read-only + plugin  
3. Phase 2 — planner tools  
4. Phase 3 — opt-in writes + wait  
5. Phase 4 — durable-agent docs/example  

Each phase is a **separate branch / PR**. Do not bundle Horizon or automations
into the MCP MVP.
