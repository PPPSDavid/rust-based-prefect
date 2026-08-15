import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";
import { ActionButton } from "../components/ActionButton";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import type { ConcurrencyLimit } from "../types";

export function ConcurrencyPage() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [limit, setLimit] = useState("");
  const [decay, setDecay] = useState("");
  const [inspected, setInspected] = useState<ConcurrencyLimit | null>(null);

  const limits = useQuery({
    queryKey: ["concurrency-limits"],
    queryFn: () => api.listConcurrencyLimits()
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["concurrency-limits"] });
  };

  const createLimit = useMutation({
    mutationFn: () => {
      const payload: {
        name: string;
        limit: number;
        slot_decay_per_second?: number;
      } = {
        name: name.trim(),
        limit: Number(limit)
      };
      if (decay.trim()) {
        payload.slot_decay_per_second = Number(decay);
      }
      return api.upsertConcurrencyLimit(payload);
    },
    onSuccess: () => {
      invalidate();
      setName("");
      setLimit("");
      setDecay("");
    }
  });

  const patchLimit = useMutation({
    mutationFn: ({ name: limitName, active }: { name: string; active: boolean }) =>
      api.patchConcurrencyLimit(limitName, { active }),
    onSuccess: (updated) => {
      invalidate();
      setInspected((current) => (current && current.name === updated.name ? updated : current));
    }
  });

  const deleteLimit = useMutation({
    mutationFn: (limitName: string) => api.deleteConcurrencyLimit(limitName),
    onSuccess: (_data, limitName) => {
      invalidate();
      setInspected((current) => (current && current.name === limitName ? null : current));
    }
  });

  if (limits.isLoading) return <p>Loading concurrency limits...</p>;

  const canCreate =
    Boolean(name.trim()) &&
    limit.trim() !== "" &&
    Number.isFinite(Number(limit)) &&
    Number(limit) >= 0 &&
    !createLimit.isPending;

  return (
    <section>
      <PageHeader
        title="Concurrency"
        subtitle="Named global slot limits (same ledger as ironflow gcl). Not deployment concurrency."
      />
      <div className="toolbar">
        <input
          className="field-input"
          aria-label="Limit name"
          placeholder="Limit name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <input
          className="field-input"
          aria-label="Slot limit"
          placeholder="Limit"
          type="number"
          min={0}
          value={limit}
          onChange={(e) => setLimit(e.target.value)}
        />
        <input
          className="field-input"
          aria-label="Slot decay per second"
          placeholder="Decay (optional)"
          type="number"
          min={0}
          step="any"
          value={decay}
          onChange={(e) => setDecay(e.target.value)}
        />
        <ActionButton variant="primary" disabled={!canCreate} onClick={() => createLimit.mutate()}>
          Create limit
        </ActionButton>
      </div>
      {createLimit.isError ? <p className="muted">{String(createLimit.error)}</p> : null}
      <DataTable
        columns={[
          {
            key: "name",
            header: "Name",
            render: (row) => (
              <ActionButton onClick={() => setInspected(row)}>{row.name}</ActionButton>
            )
          },
          { key: "limit", header: "Limit", render: (row) => String(row.limit) },
          {
            key: "active_slots",
            header: "Active slots",
            render: (row) => String(row.active_slots)
          },
          {
            key: "decay",
            header: "Decay / s",
            render: (row) =>
              row.slot_decay_per_second == null ? "—" : String(row.slot_decay_per_second)
          },
          {
            key: "active",
            header: "Active",
            render: (row) => (row.active ? "Yes" : "No")
          },
          {
            key: "actions",
            header: "Actions",
            render: (row) => (
              <div className="chip-row">
                <ActionButton
                  disabled={patchLimit.isPending}
                  onClick={() => patchLimit.mutate({ name: row.name, active: !row.active })}
                >
                  {row.active ? "Deactivate" : "Activate"}
                </ActionButton>
                <ActionButton
                  variant="danger"
                  disabled={deleteLimit.isPending}
                  onClick={() => deleteLimit.mutate(row.name)}
                >
                  Delete
                </ActionButton>
              </div>
            )
          }
        ]}
        rows={limits.data?.limits ?? []}
        rowKey={(row) => row.id || row.name}
        emptyMessage="No concurrency limits yet."
      />
      {inspected ? (
        <pre className="task-result" aria-label="Limit inspect payload">
          {JSON.stringify(inspected, null, 2)}
        </pre>
      ) : null}
    </section>
  );
}
