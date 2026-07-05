import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { ActionButton } from "../components/ActionButton";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";

export function WorkPoolsPage() {
  const queryClient = useQueryClient();
  const [newPoolName, setNewPoolName] = useState("");
  const pools = useQuery({ queryKey: ["work-pools"], queryFn: () => api.listWorkPools() });
  const createPool = useMutation({
    mutationFn: (name: string) => api.createWorkPool({ name, type: "process" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["work-pools"] });
      setNewPoolName("");
    }
  });

  if (pools.isLoading) return <p>Loading work pools...</p>;

  return (
    <section>
      <PageHeader title="Work Pools" subtitle="Infrastructure pools that workers poll for runs." />
      <div className="toolbar">
        <input
          className="field-input"
          placeholder="New pool name"
          value={newPoolName}
          onChange={(e) => setNewPoolName(e.target.value)}
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
        rows={pools.data?.items ?? []}
        rowKey={(pool) => pool.id}
        emptyMessage="No work pools yet."
      />
    </section>
  );
}
