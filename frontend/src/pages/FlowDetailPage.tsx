import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { ActionButton } from "../components/ActionButton";
import { DataTable } from "../components/DataTable";
import { ErrorBanner } from "../components/ErrorBanner";
import { PageHeader } from "../components/PageHeader";
import { StateBadge } from "../components/StateBadge";

export function FlowDetailPage() {
  const { name = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const flow = useQuery({
    queryKey: ["flow", name],
    queryFn: () => api.getFlow(name),
    enabled: Boolean(name)
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["flow"] });
    void queryClient.invalidateQueries({ queryKey: ["flows"] });
  };

  const rename = useMutation({
    mutationFn: (next: string) => api.renameFlow(flow.data?.id ?? "", next),
    onSuccess: (updated) => {
      setError(null);
      invalidate();
      if (updated.name && updated.name !== name) {
        void navigate(`/flows/${encodeURIComponent(updated.name)}`);
      }
    },
    onError: (err: Error) => setError(err.message)
  });
  const archive = useMutation({
    mutationFn: () => api.archiveFlow(flow.data?.id ?? ""),
    onSuccess: () => {
      setError(null);
      invalidate();
    },
    onError: (err: Error) => setError(err.message)
  });
  const restore = useMutation({
    mutationFn: () => api.restoreFlow(flow.data?.id ?? ""),
    onSuccess: () => {
      setError(null);
      invalidate();
    },
    onError: (err: Error) => setError(err.message)
  });
  const remove = useMutation({
    mutationFn: () => api.deleteFlow(flow.data?.id ?? ""),
    onSuccess: () => {
      setError(null);
      invalidate();
      void navigate("/flows");
    },
    onError: (err: Error) => setError(err.message)
  });

  if (flow.isLoading) return <p>Loading flow...</p>;
  if (!flow.data) return <p>Flow not found.</p>;

  const detail = flow.data;
  const flowDeployments = detail.deployments ?? [];
  const flowRuns = detail.recent_runs ?? [];
  const isArchived = detail.status === "archived";

  return (
    <section>
      <PageHeader
        title={detail.name}
        subtitle="Flow definition and recent activity."
        breadcrumbs={[
          { label: "Flows", to: "/flows" },
          { label: detail.name }
        ]}
        actions={
          <>
            <ActionButton
              onClick={() => {
                const next = window.prompt("New canonical name", detail.name);
                if (next && next !== detail.name) rename.mutate(next);
              }}
            >
              Rename
            </ActionButton>
            {isArchived ? (
              <ActionButton onClick={() => restore.mutate()}>Restore</ActionButton>
            ) : (
              <ActionButton onClick={() => archive.mutate()}>Archive</ActionButton>
            )}
            <ActionButton
              variant="danger"
              onClick={() => {
                if (window.confirm(`Soft-delete flow ${detail.name}?`)) remove.mutate();
              }}
            >
              Delete
            </ActionButton>
          </>
        }
      />
      {error ? <ErrorBanner message={error} /> : null}
      {detail.resolved_from_alias ? (
        <p>
          <strong>{detail.requested_name}</strong> is now an alias of <strong>{detail.canonical_name ?? detail.name}</strong>.
        </p>
      ) : null}
      {(detail.aliases ?? []).length > 0 ? (
        <p>Former names: {(detail.aliases ?? []).join(", ")}</p>
      ) : null}
      <h3>Tasks</h3>
      <DataTable
        columns={[
          { key: "task", header: "Task", render: (task) => task.task_name },
          { key: "runs", header: "Runs", render: (task) => task.run_count }
        ]}
        rows={detail.tasks}
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
