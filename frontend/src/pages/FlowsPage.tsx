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
                {deployment.name} [{deployment.flow_name}]{" "}
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
