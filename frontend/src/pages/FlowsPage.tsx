import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";

export function FlowsPage() {
  const flows = useQuery({ queryKey: ["flows"], queryFn: () => api.listFlows() });

  if (flows.isLoading) return <p>Loading flows...</p>;

  return (
    <section>
      <PageHeader title="Flows" subtitle="Registered flow definitions." />
      <DataTable
        columns={[
          {
            key: "name",
            header: "Flow",
            render: (flow) => <Link to={`/flows/${encodeURIComponent(flow.name)}`}>{flow.name}</Link>
          },
          { key: "runs", header: "Runs", render: (flow) => flow.run_count },
          {
            key: "updated",
            header: "Last activity",
            render: (flow) => new Date(flow.updated_at).toLocaleString()
          }
        ]}
        rows={flows.data?.items ?? []}
        rowKey={(flow) => flow.name}
      />
    </section>
  );
}
