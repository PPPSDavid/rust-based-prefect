import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { ActionButton } from "../components/ActionButton";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import { QuickRunModal } from "../components/QuickRunModal";
import { StateBadge } from "../components/StateBadge";

export function DeploymentDetailPage() {
  const { id = "" } = useParams();
  const queryClient = useQueryClient();
  const [showQuickRun, setShowQuickRun] = useState(false);
  const deployment = useQuery({
    queryKey: ["deployment", id],
    queryFn: () => api.getDeployment(id),
    enabled: Boolean(id)
  });
  const deploymentRuns = useQuery({
    queryKey: ["deployment-runs", id],
    queryFn: () => api.listDeploymentRuns(id),
    enabled: Boolean(id)
  });
  const togglePause = useMutation({
    mutationFn: (paused: boolean) => api.patchDeployment(id, { paused }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["deployment", id] })
  });
  const trigger = useMutation({
    mutationFn: (payload?: { parameters?: Record<string, unknown>; idempotency_key?: string }) =>
      api.triggerDeploymentRun(id, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["deployment-runs", id] });
      void queryClient.invalidateQueries({ queryKey: ["flow-runs"] });
      setShowQuickRun(false);
    }
  });

  if (deployment.isLoading) return <p>Loading deployment...</p>;
  if (!deployment.data) return <p>Deployment not found.</p>;

  const dep = deployment.data;

  return (
    <section>
      <PageHeader
        title={dep.name}
        subtitle={`Flow: ${dep.flow_name}`}
        breadcrumbs={[
          { label: "Deployments", to: "/deployments" },
          { label: dep.name }
        ]}
        actions={
          <>
            <ActionButton onClick={() => togglePause.mutate(!dep.paused)}>
              {dep.paused ? "Resume" : "Pause"}
            </ActionButton>
            <ActionButton variant="primary" disabled={dep.paused} onClick={() => setShowQuickRun(true)}>
              Quick Run
            </ActionButton>
          </>
        }
      />
      <dl className="detail-grid">
        <dt>Status</dt>
        <dd>{dep.paused ? "Paused" : "Active"}</dd>
        <dt>Concurrency</dt>
        <dd>{dep.concurrency_limit ?? "Unlimited"}</dd>
        <dt>Collision strategy</dt>
        <dd>{dep.collision_strategy ?? "ENQUEUE"}</dd>
        <dt>Work pool</dt>
        <dd>{dep.work_pool_id ?? "default-process-pool"}</dd>
        <dt>Default parameters</dt>
        <dd className="mono-list">{JSON.stringify(dep.default_parameters)}</dd>
      </dl>
      <h3>Recent deployment runs</h3>
      <DataTable
        columns={[
          { key: "status", header: "Status", render: (run) => <StateBadge state={run.status} /> },
          {
            key: "flow_run",
            header: "Flow run",
            render: (run) =>
              run.flow_run_id ? <Link to={`/runs/${run.flow_run_id}`}>{run.flow_run_id.slice(0, 8)}</Link> : "—"
          },
          {
            key: "created",
            header: "Created",
            render: (run) => new Date(run.created_at).toLocaleString()
          }
        ]}
        rows={deploymentRuns.data?.items ?? []}
        rowKey={(run) => run.id}
      />
      {showQuickRun ? (
        <QuickRunModal
          deploymentName={dep.name}
          defaultParameters={dep.default_parameters}
          isPending={trigger.isPending}
          onClose={() => setShowQuickRun(false)}
          onSubmit={(payload) => trigger.mutate(payload)}
        />
      ) : null}
    </section>
  );
}
