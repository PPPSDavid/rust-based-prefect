import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
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

const STATUS_VALUES = ["active", "archived"] as const;
const SORT_KEYS = ["updated", "name", "runs"] as const;
const DEFAULT_SORT: SortSpec = { key: "updated", dir: "desc" };
const SORT_OPTIONS: ListSortOption[] = [
  { value: "updated:desc", label: "Last activity (newest)" },
  { value: "updated:asc", label: "Last activity (oldest)" },
  { value: "name:asc", label: "Name (A-Z)" },
  { value: "name:desc", label: "Name (Z-A)" },
  { value: "runs:desc", label: "Runs (most)" },
  { value: "runs:asc", label: "Runs (fewest)" }
];

type FlowRow = {
  id?: string;
  name: string;
  status?: string;
  run_count: number;
  updated_at: string;
};

export function FlowsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const status = readChoice(searchParams, "status", STATUS_VALUES, "active");
  const query = readQuery(searchParams);
  const sort = readSort(searchParams, SORT_KEYS, DEFAULT_SORT);
  const flows = useQuery({
    queryKey: ["flows", status],
    queryFn: () => collectPages((cursor) => api.listFlows(cursor, status))
  });

  const rows = useMemo(() => {
    const filtered = filterByText(flows.data ?? [], query, (flow) => flow.name);
    return sortRows(filtered, sort, flowSortValue);
  }, [flows.data, query, sort]);

  const commit = (mutate: (params: URLSearchParams) => void) => {
    const next = new URLSearchParams(searchParams);
    mutate(next);
    setSearchParams(next, { replace: true });
  };

  if (flows.isLoading) return <p>Loading flows...</p>;

  return (
    <section>
      <PageHeader title="Flows" subtitle="UUID-stable catalog. Rename keeps history; archive hides without deleting." />
      {flows.isError ? <ErrorBanner message="Failed to load flows." /> : null}
      <ListControls
        chipGroupLabel="Flow catalog"
        chips={[
          { id: "active", label: "Active" },
          { id: "archived", label: "Archived" }
        ]}
        chip={status}
        onChip={(next) => commit((params) => writeChoice(params, "status", next, "active"))}
        searchLabel="Search flows"
        searchPlaceholder="Search flow name"
        query={query}
        onQuery={(next) => commit((params) => writeQuery(params, next))}
        sort={encodeSort(sort)}
        sortOptions={SORT_OPTIONS}
        onSort={(next) => commit((params) => writeSort(params, decodeSort(next, SORT_KEYS, DEFAULT_SORT), DEFAULT_SORT))}
      />
      <DataTable
        columns={[
          {
            key: "name",
            header: "Flow",
            render: (flow) => <Link to={`/flows/${encodeURIComponent(flow.name)}`}>{flow.name}</Link>
          },
          { key: "status", header: "Status", render: (flow) => flow.status ?? status },
          { key: "runs", header: "Runs", render: (flow) => flow.run_count },
          {
            key: "updated",
            header: "Last activity",
            render: (flow) => new Date(flow.updated_at).toLocaleString()
          }
        ]}
        rows={rows}
        rowKey={(flow) => flow.id ?? flow.name}
        emptyMessage={emptyMessage(status, query)}
      />
    </section>
  );
}

function flowSortValue(flow: FlowRow, key: string): string | number {
  if (key === "name") return flow.name;
  if (key === "runs") return flow.run_count;
  return Date.parse(flow.updated_at) || 0;
}

function emptyMessage(status: string, query: string): string {
  if (query.trim()) return "No flows match this view.";
  return status === "archived" ? "No archived flows." : "No active flows.";
}
