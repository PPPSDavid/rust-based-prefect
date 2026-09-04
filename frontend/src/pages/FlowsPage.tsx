import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";

const PANELS = ["active", "archived"] as const;

export function FlowsPage() {
  const [panel, setPanel] = useState<(typeof PANELS)[number]>("active");
  const flows = useQuery({
    queryKey: ["flows", panel],
    queryFn: () => api.listFlows(undefined, panel)
  });

  if (flows.isLoading) return <p>Loading flows...</p>;

  return (
    <section>
      <PageHeader title="Flows" subtitle="UUID-stable catalog. Rename keeps history; archive hides without deleting." />
      <div className="chip-row">
        {PANELS.map((value) => (
          <button
            key={value}
            type="button"
            className={panel === value ? "chip chip-active" : "chip"}
            onClick={() => setPanel(value)}
          >
            {value === "active" ? "Active" : "Archived"}
          </button>
        ))}
      </div>
      <DataTable
        columns={[
          {
            key: "name",
            header: "Flow",
            render: (flow) => <Link to={`/flows/${encodeURIComponent(flow.name)}`}>{flow.name}</Link>
          },
          { key: "status", header: "Status", render: (flow) => flow.status ?? panel },
          { key: "runs", header: "Runs", render: (flow) => flow.run_count },
          {
            key: "updated",
            header: "Last activity",
            render: (flow) => new Date(flow.updated_at).toLocaleString()
          }
        ]}
        rows={flows.data?.items ?? []}
        rowKey={(flow) => flow.id ?? flow.name}
        emptyMessage={panel === "archived" ? "No archived flows." : "No active flows."}
      />
    </section>
  );
}
