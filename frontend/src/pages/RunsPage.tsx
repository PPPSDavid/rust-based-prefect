import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { DataTable } from "../components/DataTable";
import { ErrorBanner } from "../components/ErrorBanner";
import { PageHeader } from "../components/PageHeader";
import { StateBadge } from "../components/StateBadge";
import { useSsePulse } from "../hooks/useSsePulse";
import type { FlowRun } from "../types";

const STATE_FILTERS = ["ALL", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"] as const;

export function RunsPage() {
  const queryClient = useQueryClient();
  const [stateFilter, setStateFilter] = useState<(typeof STATE_FILTERS)[number]>("ALL");
  const [nameQuery, setNameQuery] = useState("");
  const [cursor, setCursor] = useState<string | undefined>();
  const [allItems, setAllItems] = useState<FlowRun[]>([]);

  const openFlowRunsStream = useCallback(() => api.streamFlowRuns(), []);
  const pulse = useSsePulse(openFlowRunsStream);

  useEffect(() => {
    if (pulse > 0) {
      void queryClient.invalidateQueries({ queryKey: ["flow-runs"] });
    }
  }, [pulse, queryClient]);

  const { data, isLoading, error, isFetching } = useQuery({
    queryKey: ["flow-runs", stateFilter, cursor],
    queryFn: () => api.listFlowRuns(cursor, stateFilter === "ALL" ? undefined : stateFilter),
    staleTime: 5_000
  });

  useEffect(() => {
    if (!data) return;
    setAllItems((prev) => {
      if (!cursor) return data.items;
      const seen = new Set(prev.map((r) => r.id));
      const appended = data.items.filter((r) => !seen.has(r.id));
      return appended.length ? [...prev, ...appended] : prev;
    });
  }, [data, cursor]);

  const selectFilter = (state: (typeof STATE_FILTERS)[number]) => {
    setStateFilter(state);
    setCursor(undefined);
    setAllItems([]);
  };

  // Prefer live query data for the first page so remounts with a warm cache
  // never render an empty table while allItems is still catching up.
  const sourceItems = !cursor && data?.items ? data.items : allItems;

  const filtered = useMemo(() => {
    const q = nameQuery.trim().toLowerCase();
    if (!q) return sourceItems;
    return sourceItems.filter((run) => run.name.toLowerCase().includes(q));
  }, [sourceItems, nameQuery]);

  if (isLoading && sourceItems.length === 0) return <p>Loading runs...</p>;

  return (
    <section>
      <PageHeader title="Flow Runs" subtitle="Monitor and manage flow execution." />
      {error ? <ErrorBanner message="Failed to load runs." /> : null}
      <div className="toolbar">
        <input
          className="field-input"
          placeholder="Search by flow name"
          value={nameQuery}
          onChange={(e) => setNameQuery(e.target.value)}
        />
        <div className="chip-row">
          {STATE_FILTERS.map((state) => (
            <button
              key={state}
              type="button"
              className={stateFilter === state ? "chip chip-active" : "chip"}
              onClick={() => selectFilter(state)}
            >
              {state}
            </button>
          ))}
        </div>
      </div>
      <DataTable
        columns={[
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
          },
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
            key: "state",
            header: "State",
            render: (run) => <StateBadge state={run.state} />
          },
          { key: "version", header: "Version", render: (run) => run.version },
          {
            key: "updated",
            header: "Updated",
            render: (run) => new Date(run.updated_at).toLocaleString()
          }
        ]}
        rows={filtered}
        rowKey={(run) => run.id}
        emptyMessage="No flow runs match the current filters."
      />
      {data?.next_cursor ? (
        <div className="load-more">
          <button type="button" onClick={() => setCursor(data.next_cursor ?? undefined)} disabled={isFetching}>
            {isFetching ? "Loading..." : "Load more"}
          </button>
        </div>
      ) : null}
    </section>
  );
}
