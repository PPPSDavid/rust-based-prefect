import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { ActionButton } from "../components/ActionButton";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import { StateBadge } from "../components/StateBadge";

export function WorkPoolDetailPage() {
  const { id = "" } = useParams();
  const queryClient = useQueryClient();
  const pool = useQuery({ queryKey: ["work-pool", id], queryFn: () => api.getWorkPool(id), enabled: Boolean(id) });
  const workers = useQuery({
    queryKey: ["workers", id],
    queryFn: () => api.listWorkers(id),
    enabled: Boolean(id),
    refetchInterval: 15_000
  });
  const togglePause = useMutation({
    mutationFn: (paused: boolean) => api.patchWorkPool(id, { paused }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["work-pool", id] })
  });

  if (pool.isLoading) return <p>Loading work pool...</p>;
  if (!pool.data) return <p>Work pool not found.</p>;

  return (
    <section>
      <PageHeader
        title={pool.data.name}
        subtitle={`Type: ${pool.data.type}`}
        breadcrumbs={[
          { label: "Work Pools", to: "/work-pools" },
          { label: pool.data.name }
        ]}
        actions={
          <ActionButton onClick={() => togglePause.mutate(!pool.data.paused)}>
            {pool.data.paused ? "Resume pool" : "Pause pool"}
          </ActionButton>
        }
      />
      <p className="muted">Status: {pool.data.paused ? "Paused" : "Ready"}</p>
      <h3>Workers</h3>
      <DataTable
        columns={[
          { key: "name", header: "Worker", render: (worker) => worker.name },
          { key: "status", header: "Status", render: (worker) => <StateBadge state={worker.status} /> },
          {
            key: "heartbeat",
            header: "Last heartbeat",
            render: (worker) => new Date(worker.last_heartbeat).toLocaleString()
          }
        ]}
        rows={workers.data?.items ?? []}
        rowKey={(worker) => worker.name}
        emptyMessage="No workers have polled this pool recently."
      />
    </section>
  );
}
