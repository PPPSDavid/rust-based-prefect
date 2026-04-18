import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";

export function FlowsPage() {
  const queryClient = useQueryClient();
  const flows = useQuery({ queryKey: ["flows"], queryFn: () => api.listFlows() });
  const tasks = useQuery({ queryKey: ["tasks"], queryFn: () => api.listTasks() });
  const deployments = useQuery({ queryKey: ["deployments"], queryFn: () => api.listDeployments() });
  const trigger = useMutation({
    mutationFn: (deploymentId: string) => api.triggerDeploymentRun(deploymentId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["flow-runs"] });
      void queryClient.invalidateQueries({ queryKey: ["deployments"] });
    }
  });

  return (
    <section>
      <h2>Flows</h2>
      <div className="split">
        <div>
          <h3>Flow Catalog</h3>
          <ul>
            {flows.data?.items.map((flow) => (
              <li key={flow.name}>
                {flow.name} ({flow.run_count})
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3>Task Catalog</h3>
          <ul>
            {tasks.data?.map((task) => (
              <li key={task.task_name}>
                {task.task_name} ({task.run_count})
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3>Deployments</h3>
          <ul>
            {deployments.data?.items.map((deployment) => (
              <li key={deployment.id}>
                {deployment.name} [{deployment.flow_name}]
                {deployment.schedule_enabled ? (
                  <span style={{ opacity: 0.85 }}>
                    {" "}
                    — schedule:{" "}
                    {deployment.schedule_cron && deployment.schedule_cron.trim() !== ""
                      ? `cron ${deployment.schedule_cron}`
                      : deployment.schedule_interval_seconds != null
                        ? `every ${deployment.schedule_interval_seconds}s`
                        : "on"}
                    {deployment.schedule_next_run_at != null && deployment.schedule_next_run_at !== ""
                      ? `, next ${deployment.schedule_next_run_at}`
                      : ""}
                  </span>
                ) : null}
                {" "}
                <button
                  onClick={() => trigger.mutate(deployment.id)}
                  disabled={deployment.paused || trigger.isPending}
                >
                  Run now
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
