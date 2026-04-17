import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useSsePulse } from "../hooks/useSsePulse";

export function RunsPage() {
  const queryClient = useQueryClient();
  const openFlowRunsStream = useCallback(() => api.streamFlowRuns(), []);
  const pulse = useSsePulse(openFlowRunsStream);

  useEffect(() => {
    if (pulse > 0) {
      void queryClient.invalidateQueries({ queryKey: ["flow-runs"] });
    }
  }, [pulse, queryClient]);

  const { data, isLoading, error } = useQuery({
    queryKey: ["flow-runs"],
    queryFn: () => api.listFlowRuns(),
    staleTime: 5_000
  });

  if (isLoading && !data) return <p>Loading runs...</p>;
  if (error && !data) return <p>Failed to load runs.</p>;

  return (
    <section>
      <h2>Flow Runs</h2>
      <table className="grid">
        <thead>
          <tr>
            <th>Name</th>
            <th>State</th>
            <th>Version</th>
            <th>Updated</th>
          </tr>
        </thead>
        <tbody>
          {data?.items.map((run) => (
            <tr key={run.id}>
              <td>
                <Link to={`/runs/${run.id}`}>{run.name}</Link>
              </td>
              <td>
                <span className={`badge badge-${run.state.toLowerCase()}`}>{run.state}</span>
              </td>
              <td>{run.version}</td>
              <td>{new Date(run.updated_at).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
