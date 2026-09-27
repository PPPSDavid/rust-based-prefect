import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { ActionButton } from "../components/ActionButton";
import { ErrorBanner } from "../components/ErrorBanner";
import { DeploymentGroupTable } from "../components/list/DeploymentGroupTable";
import { collectPages } from "../components/list/collectPages";
import {
  filterDeployments,
  groupDeployments,
  type DeploymentStatusFilter
} from "../components/list/deploymentGroups";
import { ListControls, type ListSortOption } from "../components/list/ListControls";
import {
  decodeSort,
  encodeSort,
  readChoice,
  readQuery,
  readSort,
  writeChoice,
  writeQuery,
  writeSort,
  type SortSpec
} from "../components/list/listQuery";
import { PageHeader } from "../components/PageHeader";
import { QuickRunModal } from "../components/QuickRunModal";
import type { Deployment } from "../types";

const STATUS_VALUES = ["all", "active", "paused"] as const;
const SORT_KEYS = ["recent", "name", "flow", "updated"] as const;
const DEFAULT_SORT: SortSpec = { key: "recent", dir: "desc" };
const SORT_OPTIONS: ListSortOption[] = [
  { value: "recent:desc", label: "Recent" },
  { value: "name:asc", label: "Name (A-Z)" },
  { value: "name:desc", label: "Name (Z-A)" },
  { value: "flow:asc", label: "Flow (A-Z)" },
  { value: "flow:desc", label: "Flow (Z-A)" },
  { value: "updated:desc", label: "Updated (newest)" },
  { value: "updated:asc", label: "Updated (oldest)" }
];

export function DeploymentsPage() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [quickRun, setQuickRun] = useState<Deployment | null>(null);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() => new Set());
  const status = readChoice(searchParams, "status", STATUS_VALUES, "all") as DeploymentStatusFilter;
  const query = readQuery(searchParams);
  const sort = readSort(searchParams, SORT_KEYS, DEFAULT_SORT);
  const deployments = useQuery({
    queryKey: ["deployments"],
    queryFn: () => collectPages((cursor) => api.listDeployments(cursor))
  });
  const trigger = useMutation({
    mutationFn: ({
      deploymentId,
      payload
    }: {
      deploymentId: string;
      payload?: { parameters?: Record<string, unknown>; idempotency_key?: string };
    }) => api.triggerDeploymentRun(deploymentId, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["flow-runs"] });
      void queryClient.invalidateQueries({ queryKey: ["deployment-runs"] });
      setQuickRun(null);
    }
  });

  const groups = useMemo(
    () => groupDeployments(filterDeployments(deployments.data ?? [], query, status), sort),
    [deployments.data, query, status, sort]
  );

  const commit = (mutate: (params: URLSearchParams) => void) => {
    const next = new URLSearchParams(searchParams);
    mutate(next);
    setSearchParams(next, { replace: true });
  };

  const toggleGroup = (flowName: string) => {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(flowName)) next.delete(flowName);
      else next.add(flowName);
      return next;
    });
  };

  if (deployments.isLoading) return <p>Loading deployments...</p>;

  return (
    <section>
      <PageHeader title="Deployments" subtitle="Schedule and trigger flow deployments." />
      {deployments.isError ? <ErrorBanner message="Failed to load deployments." /> : null}
      <ListControls
        chipGroupLabel="Deployment status"
        chips={[
          { id: "all", label: "All" },
          { id: "active", label: "Active" },
          { id: "paused", label: "Paused" }
        ]}
        chip={status}
        onChip={(next) => commit((params) => writeChoice(params, "status", next, "all"))}
        searchLabel="Search deployments"
        searchPlaceholder="Search flow or deployment"
        query={query}
        onQuery={(next) => commit((params) => writeQuery(params, next))}
        sort={encodeSort(sort)}
        sortOptions={SORT_OPTIONS}
        onSort={(next) => commit((params) => writeSort(params, decodeSort(next, SORT_KEYS, DEFAULT_SORT), DEFAULT_SORT))}
        note="Deployments that share a flow stay together. Each row keeps its name, schedule, and status."
      />
      <DeploymentGroupTable
        groups={groups}
        collapsed={collapsed}
        onToggle={toggleGroup}
        emptyMessage={query.trim() || status !== "all" ? "No deployments match this view." : "No deployments yet."}
        renderActions={(dep) => (
          <ActionButton variant="primary" disabled={dep.paused || trigger.isPending} onClick={() => setQuickRun(dep)}>
            Quick Run
          </ActionButton>
        )}
      />
      {quickRun ? (
        <QuickRunModal
          deploymentName={quickRun.name}
          defaultParameters={quickRun.default_parameters}
          isPending={trigger.isPending}
          onClose={() => setQuickRun(null)}
          onSubmit={(payload) => trigger.mutate({ deploymentId: quickRun.id, payload })}
        />
      ) : null}
      {trigger.isError ? <p className="form-error">Failed to start deployment run.</p> : null}
    </section>
  );
}
