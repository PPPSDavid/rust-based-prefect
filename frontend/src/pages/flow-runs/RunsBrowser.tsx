import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../../api";
import { DataTable, type Column } from "../../components/DataTable";
import { ErrorBanner } from "../../components/ErrorBanner";
import { PageHeader } from "../../components/PageHeader";
import { StateBadge } from "../../components/StateBadge";
import { useSsePulse } from "../../hooks/useSsePulse";
import { formatRunDuration } from "../../runLifecycle";
import type { FlowRun } from "../../types";
import {
  BUILT_IN_VIEWS,
  STATE_CHIPS,
  filtersFromSearchParams,
  filtersToSearchParams,
  resolvedState,
  toApiQuery,
  withStateChip,
  type RunListFilters,
  type TimeRange
} from "./filters";

export function RunsBrowser() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => filtersFromSearchParams(searchParams), [searchParams]);
  const filterKey = searchParams.toString();
  const [cursorState, setCursorState] = useState<{ key: string; cursor?: string }>({ key: filterKey });
  if (cursorState.key !== filterKey) {
    setCursorState({ key: filterKey });
  }
  const cursor = cursorState.key === filterKey ? cursorState.cursor : undefined;
  const [pages, setPages] = useState<{ key: string; items: FlowRun[] }>({ key: "", items: [] });

  const openFlowRunsStream = useCallback(() => api.streamFlowRuns(), []);
  const pulse = useSsePulse(openFlowRunsStream);

  useEffect(() => {
    if (pulse > 0) {
      setCursorState({ key: filterKey });
      void queryClient.invalidateQueries({ queryKey: ["flow-runs"] });
    }
  }, [pulse, queryClient, filterKey]);

  const { data, isLoading, error, isFetching } = useQuery({
    queryKey: ["flow-runs", filterKey, cursor ?? ""],
    queryFn: () =>
      api.listFlowRuns(toApiQuery(filtersFromSearchParams(searchParams), new Date(), cursor)),
    staleTime: 5_000
  });

  useEffect(() => {
    if (!data) return;
    setPages((prev) => {
      if (!cursor) return { key: filterKey, items: data.items };
      const base = prev.key === filterKey ? prev.items : [];
      const seen = new Set(base.map((item) => item.id));
      const extra = data.items.filter((item) => !seen.has(item.id));
      return { key: filterKey, items: extra.length ? [...base, ...extra] : base };
    });
  }, [data, cursor, filterKey]);

  const commit = (next: RunListFilters) => {
    setSearchParams(filtersToSearchParams(next), { replace: true });
  };

  const rows = !cursor && data?.items ? data.items : pages.key === filterKey ? pages.items : [];
  const activeState = resolvedState(filters);

  if (isLoading && rows.length === 0) return <p>Loading runs...</p>;

  return (
    <section>
      <PageHeader
        title="Flow Runs"
        subtitle="Search runs by name, flow, deployment, or tag, and share the view."
      />
      {error ? <ErrorBanner message="Failed to load runs." /> : null}
      <div className="toolbar" role="group" aria-label="Views">
        <button
          type="button"
          className={!filters.view && !filters.state ? "chip chip-active" : "chip"}
          aria-pressed={!filters.view && !filters.state}
          onClick={() => commit(withStateChip(filters, null))}
        >
          All
        </button>
        {BUILT_IN_VIEWS.map((view) => (
          <button
            key={view.id}
            type="button"
            className={filters.view === view.id ? "chip chip-active" : "chip"}
            aria-pressed={filters.view === view.id}
            onClick={() => commit(withStateChip(filters, view.state))}
          >
            {view.label}
          </button>
        ))}
      </div>
      <div className="toolbar">
        <input
          className="field-input run-search"
          type="search"
          aria-label="Search runs"
          placeholder="Search name, flow, deployment, or tag"
          value={filters.q}
          onChange={(event) => commit({ ...filters, q: event.target.value })}
        />
        <label>
          Time range{" "}
          <select
            className="field-input"
            aria-label="Time range"
            value={filters.range}
            onChange={(event) => commit({ ...filters, range: event.target.value as TimeRange })}
          >
            <option value="any">Any time</option>
            <option value="1h">Past hour</option>
            <option value="24h">Past day</option>
            <option value="7d">Past 7 days</option>
            <option value="custom">Custom range</option>
          </select>
        </label>
        {filters.range === "custom" ? (
          <>
            <input
              className="field-input"
              type="datetime-local"
              aria-label="Created after"
              value={filters.after}
              onChange={(event) => commit({ ...filters, after: event.target.value })}
            />
            <input
              className="field-input"
              type="datetime-local"
              aria-label="Created before"
              value={filters.before}
              onChange={(event) => commit({ ...filters, before: event.target.value })}
            />
          </>
        ) : null}
      </div>
      <p className="run-filters-note">
        Time range matches when the run was created. Duration uses deployment start and end when those times
        exist.
      </p>
      <div className="toolbar">
        <div className="chip-row" role="group" aria-label="State">
          {STATE_CHIPS.map((state) => (
            <button
              key={state}
              type="button"
              className={activeState === state ? "chip chip-active" : "chip"}
              aria-pressed={activeState === state}
              onClick={() => commit(withStateChip(filters, state))}
            >
              {state}
            </button>
          ))}
        </div>
      </div>
      <DataTable
        columns={runColumns()}
        rows={rows}
        rowKey={(run) => run.id}
        emptyMessage="No flow runs match this view."
      />
      {data?.next_cursor ? (
        <div className="load-more">
          <button
            type="button"
            onClick={() => setCursorState({ key: filterKey, cursor: data.next_cursor ?? undefined })}
            disabled={isFetching}
          >
            {isFetching ? "Loading..." : "Load more"}
          </button>
        </div>
      ) : null}
    </section>
  );
}

function runColumns(): Column<FlowRun>[] {
  return [
    {
      key: "name",
      header: "Name",
      render: (run) => (
        <span>
          <Link to={`/runs/${run.id}`}>{run.name}</Link>
          <span className="mono"> · {run.id.slice(0, 8)}…</span>
        </span>
      )
    },
    {
      key: "flow",
      header: "Flow",
      render: (run) => {
        const flowName = run.flow_name || run.name;
        return <Link to={`/flows/${encodeURIComponent(flowName)}`}>{flowName}</Link>;
      }
    },
    {
      key: "deployment",
      header: "Deployment",
      render: (run) =>
        run.deployment_id ? (
          <Link to={`/deployments/${run.deployment_id}`}>{run.deployment_name || run.deployment_id.slice(0, 8)}</Link>
        ) : (
          "—"
        )
    },
    {
      key: "tags",
      header: "Tags",
      render: (run) => (run.tags && run.tags.length > 0 ? run.tags.join(", ") : "—")
    },
    {
      key: "state",
      header: "State",
      render: (run) => <StateBadge state={run.state} />
    },
    {
      key: "start",
      header: "Start",
      render: (run) => formatTimestamp(run.start_time)
    },
    {
      key: "end",
      header: "End",
      render: (run) => formatTimestamp(run.end_time)
    },
    {
      key: "duration",
      header: "Duration",
      render: (run) =>
        run.start_time && run.end_time ? formatRunDuration(run.start_time, run.end_time) : "—"
    },
    {
      key: "context",
      header: "Context",
      render: (run) =>
        run.parent_flow_run_id ? (
          <span>
            <Link to={`/runs/${run.parent_flow_run_id}`}>subflow</Link>
            {run.execution_mode ? ` (${run.execution_mode})` : ""}
          </span>
        ) : (
          "root"
        )
    }
  ];
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString();
}
