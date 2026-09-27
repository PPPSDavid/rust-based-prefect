import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { ActionButton } from "../components/ActionButton";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import { QuickRunModal } from "../components/QuickRunModal";
import { useQuickRunLaunch } from "../hooks/useQuickRunLaunch";
import type { Deployment } from "../types";

function formatSchedule(dep: Deployment): string {
  if (!dep.schedule_enabled) return "Manual";
  if (dep.schedule_cron?.trim()) return `cron ${dep.schedule_cron}`;
  if (dep.schedule_rrule?.trim()) return `rrule ${dep.schedule_rrule}`;
  if (dep.schedule_interval_seconds != null) return `every ${dep.schedule_interval_seconds}s`;
  return "Scheduled";
}

export function DeploymentsPage() {
  const [quickRun, setQuickRun] = useState<Deployment | null>(null);
  const deployments = useQuery({ queryKey: ["deployments"], queryFn: () => api.listDeployments() });
  const quick = useQuickRunLaunch(() => setQuickRun(null));

  if (deployments.isLoading) return <p>Loading deployments...</p>;

  return (
    <section>
      <PageHeader title="Deployments" subtitle="Schedule and trigger flow deployments." />
      <DataTable
        columns={[
          {
            key: "name",
            header: "Name",
            render: (dep) => <Link to={`/deployments/${dep.id}`}>{dep.name}</Link>
          },
          { key: "flow", header: "Flow", render: (dep) => dep.flow_name },
          { key: "schedule", header: "Schedule", render: (dep) => formatSchedule(dep) },
          {
            key: "status",
            header: "Status",
            render: (dep) => (dep.paused ? "Paused" : "Active")
          },
          {
            key: "actions",
            header: "Actions",
            render: (dep) => (
              <ActionButton
                variant="primary"
                disabled={dep.paused || quick.launch.isPending}
                onClick={() => setQuickRun(dep)}
              >
                Quick Run
              </ActionButton>
            )
          }
        ]}
        rows={deployments.data?.items ?? []}
        rowKey={(dep) => dep.id}
      />
      {quickRun ? (
        <QuickRunModal
          deploymentName={quickRun.name}
          defaultParameters={quickRun.default_parameters}
          isPending={quick.launch.isPending}
          onClose={() => setQuickRun(null)}
          onSubmit={(payload) => quick.launch.mutate({ deploymentId: quickRun.id, payload })}
        />
      ) : null}
      {quick.notice ? <p className="form-error">{quick.notice}</p> : null}
      {quick.launch.isError ? <p className="form-error">Failed to start deployment run.</p> : null}
    </section>
  );
}
