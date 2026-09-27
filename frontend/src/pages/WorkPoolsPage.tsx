import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { ActionButton } from "../components/ActionButton";
import { DataTable } from "../components/DataTable";
import { ErrorBanner } from "../components/ErrorBanner";
import { collectPages } from "../components/list/collectPages";
import { ListControls, type ListSortOption } from "../components/list/ListControls";
import {
  decodeSort,
  encodeSort,
  filterByText,
  readChoice,
  readQuery,
  readSort,
  sortRows,
  writeChoice,
  writeQuery,
  writeSort,
  type SortSpec
} from "../components/list/listQuery";
import { PageHeader } from "../components/PageHeader";
import type { WorkPool } from "../types";

const STATUS_VALUES = ["all", "ready", "paused"] as const;
const SORT_KEYS = ["recent", "name", "updated"] as const;
const DEFAULT_SORT: SortSpec = { key: "recent", dir: "desc" };
const SORT_OPTIONS: ListSortOption[] = [
  { value: "recent:desc", label: "Recent" },
  { value: "name:asc", label: "Name (A-Z)" },
  { value: "name:desc", label: "Name (Z-A)" },
  { value: "updated:desc", label: "Updated (newest)" },
  { value: "updated:asc", label: "Updated (oldest)" }
];

export function WorkPoolsPage() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [newPoolName, setNewPoolName] = useState("");
  const status = readChoice(searchParams, "status", STATUS_VALUES, "all");
  const query = readQuery(searchParams);
  const sort = readSort(searchParams, SORT_KEYS, DEFAULT_SORT);
  const pools = useQuery({
    queryKey: ["work-pools"],
    queryFn: () => collectPages((cursor) => api.listWorkPools(cursor))
  });
  const createPool = useMutation({
    mutationFn: (name: string) => api.createWorkPool({ name, type: "process" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["work-pools"] });
      setNewPoolName("");
    }
  });

  const rows = useMemo(() => {
    const filtered = filterPools(pools.data ?? [], query, status);
    return sortRows(filtered, sort, poolSortValue);
  }, [pools.data, query, status, sort]);

  const commit = (mutate: (params: URLSearchParams) => void) => {
    const next = new URLSearchParams(searchParams);
    mutate(next);
    setSearchParams(next, { replace: true });
  };

  if (pools.isLoading) return <p>Loading work pools...</p>;

  return (
    <section>
      <PageHeader title="Work Pools" subtitle="Infrastructure pools that workers poll for runs." />
      {pools.isError ? <ErrorBanner message="Failed to load work pools." /> : null}
      <ListControls
        chipGroupLabel="Work pool status"
        chips={[
          { id: "all", label: "All" },
          { id: "ready", label: "Ready" },
          { id: "paused", label: "Paused" }
        ]}
        chip={status}
        onChip={(next) => commit((params) => writeChoice(params, "status", next, "all"))}
        searchLabel="Search work pools"
        searchPlaceholder="Search pool name"
        query={query}
        onQuery={(next) => commit((params) => writeQuery(params, next))}
        sort={encodeSort(sort)}
        sortOptions={SORT_OPTIONS}
        onSort={(next) => commit((params) => writeSort(params, decodeSort(next, SORT_KEYS, DEFAULT_SORT), DEFAULT_SORT))}
      />
      <div className="toolbar">
        <input
          className="field-input"
          placeholder="New pool name"
          aria-label="New pool name"
          value={newPoolName}
          onChange={(event) => setNewPoolName(event.target.value)}
        />
        <ActionButton
          variant="primary"
          disabled={!newPoolName.trim() || createPool.isPending}
          onClick={() => createPool.mutate(newPoolName.trim())}
        >
          Create pool
        </ActionButton>
      </div>
      <DataTable
        columns={[
          {
            key: "name",
            header: "Name",
            render: (pool) => <Link to={`/work-pools/${pool.id}`}>{pool.name}</Link>
          },
          { key: "type", header: "Type", render: (pool) => pool.type },
          {
            key: "status",
            header: "Status",
            render: (pool) => (pool.paused ? "Paused" : "Ready")
          },
          {
            key: "updated",
            header: "Updated",
            render: (pool) => new Date(pool.updated_at).toLocaleString()
          }
        ]}
        rows={rows}
        rowKey={(pool) => pool.id}
        emptyMessage={query.trim() || status !== "all" ? "No work pools match this view." : "No work pools yet."}
      />
    </section>
  );
}

function filterPools(items: WorkPool[], query: string, status: string): WorkPool[] {
  const byStatus = items.filter((pool) => {
    if (status === "ready") return !pool.paused;
    if (status === "paused") return pool.paused;
    return true;
  });
  return filterByText(byStatus, query, (pool) => pool.name);
}

function poolSortValue(pool: WorkPool, key: string): string | number {
  if (key === "name") return pool.name;
  if (key === "updated") return Date.parse(pool.updated_at) || 0;
  return pool.name;
}
