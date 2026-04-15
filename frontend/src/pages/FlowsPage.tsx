import { useQuery } from "@tanstack/react-query";
import { api } from "../api";

export function FlowsPage() {
  const flows = useQuery({ queryKey: ["flows"], queryFn: () => api.listFlows() });
  const tasks = useQuery({ queryKey: ["tasks"], queryFn: () => api.listTasks() });

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
      </div>
    </section>
  );
}
