import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import { StateBadge } from "../components/StateBadge";

export function FlowDetailPage() {
  const { name = "" } = useParams();
  const flow = useQuery({ queryKey: ["flow", name], queryFn: () => api.getFlow(name), enabled: Boolean(name) });
  const runs = useQuery({
    queryKey: ["flow-runs", "by-flow", name],
    queryFn: () => api.listFlowRuns(),
    enabled: Boolean(name)
  });
  const deployments = useQuery({
    queryKey: ["deployments"],
    queryFn: () => api.listDeployments(),
    enabled: Boolean(name)
  });

  if (flow.isLoading) return <p>Loading flow...</p>;
  if (!flow.data) return <p>Flow not found.</p>;

  const flowRuns = (runs.data?.items ?? []).filter((run) => run.name === name).slice(0, 20);
  const flowDeployments = (deployments.data?.items ?? []).filter((dep) => dep.flow_name === name);

  return (
    <section>
      <PageHeader
        title={flow.data.name}
        subtitle="Flow definition and recent activity."
        breadcrumbs={[
          { label: "Flows", to: "/flows" },
          { label: flow.data.name }
        ]}
      />
      <h3>Tasks</h3>
      <DataTable
        columns={[
          { key: "task", header: "Task", render: (task) => task.task_name },
          { key: "runs", header: "Runs", render: (task) => task.run_count }
        ]}
        rows={flow.data.tasks}
        rowKey={(task) => task.task_name}
        emptyMessage="No tasks registered for this flow."
      />
      <h3>Deployments</h3>
      <DataTable
        columns={[
          {
            key: "name",
            header: "Deployment",
            render: (dep) => <Link to={`/deployments/${dep.id}`}>{dep.name}</Link>
          },
          {
            key: "paused",
            header: "Status",
            render: (dep) => (dep.paused ? "Paused" : "Active")
          }
        ]}
        rows={flowDeployments}
        rowKey={(dep) => dep.id}
        emptyMessage="No deployments for this flow."
      />
      <h3>Recent Runs</h3>
      <DataTable
        columns={[
          {
            key: "name",
            header: "Run",
            render: (run) => <Link to={`/runs/${run.id}`}>{run.id.slice(0, 8)}</Link>
          },
          { key: "state", header: "State", render: (run) => <StateBadge state={run.state} /> },
          {
            key: "updated",
            header: "Updated",
            render: (run) => new Date(run.updated_at).toLocaleString()
          }
        ]}
        rows={flowRuns}
        rowKey={(run) => run.id}
        emptyMessage="No runs for this flow yet."
      />
    </section>
  );
}
