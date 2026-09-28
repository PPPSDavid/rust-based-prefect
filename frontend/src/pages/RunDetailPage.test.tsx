import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { RunDetailPage } from "./RunDetailPage";

vi.mock("../hooks/useSsePulse", () => ({
  useSsePulse: () => 0
}));

vi.mock("../api", () => ({
  api: {
    streamFlowRun: vi.fn(),
    getFlowRun: vi.fn().mockResolvedValue({
      id: "run-1",
      name: "mapped_flow",
      state: "COMPLETED",
      version: 3,
      created_at: "2026-04-15T21:00:00+00:00",
      updated_at: "2026-04-15T21:00:01+00:00"
    }),
    listTaskRuns: vi.fn().mockResolvedValue({
      items: [
        {
          id: "task-1",
          flow_run_id: "run-1",
          task_name: "inc",
          planned_node_id: "n1",
          state: "COMPLETED",
          version: 2,
          created_at: "2026-04-15T21:00:00+00:00",
          updated_at: "2026-04-15T21:00:01+00:00"
        }
      ],
      next_cursor: null
    }),
    listLogs: vi.fn().mockResolvedValue({ items: [], next_cursor: null }),
    listEvents: vi.fn().mockResolvedValue({ items: [], next_cursor: null }),
    cancelFlowRun: vi.fn(),
    pauseFlowRun: vi.fn(),
    resumeFlowRun: vi.fn(),
    retryFlowRun: vi.fn(),
    listFlowArtifacts: vi.fn().mockResolvedValue([
      {
        id: "art-1",
        flow_run_id: "run-1",
        task_run_id: "task-1",
        artifact_type: "result",
        key: "inc-result",
        summary: JSON.stringify({ task_name: "inc", result: 42, persisted: true }),
        created_at: "2026-04-15T21:00:01+00:00"
      }
    ]),
    getFlowRunDag: vi.fn().mockResolvedValue({
      flow_run_id: "run-1",
      mode: "logical",
      source: "forecast",
      fallback_required: false,
      warnings: [],
      forecast: {},
      nodes: [{ id: "n1", label: "inc", task_name: "inc", state: "COMPLETED" }],
      edges: []
    })
  }
}));

function renderPage() {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/runs/run-1"]}>
        <Routes>
          <Route path="/runs/:id" element={<RunDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("RunDetailPage", () => {
  it("renders DAG tab and DAG content", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "mapped_flow" })).toBeInTheDocument();
    const dagButton = screen.getByRole("tab", { name: "DAG" });
    dagButton.click();
    expect(await screen.findByText(/source:/i)).toBeInTheDocument();
    expect(await screen.findByText("inc")).toBeInTheDocument();
  });

  it("shows persisted JSON task results on the Task Runs tab", async () => {
    renderPage();
    expect(await screen.findByText(/inc - COMPLETED/i)).toBeInTheDocument();
    expect(await screen.findByText("42")).toBeInTheDocument();
  });

  it("shows drain and terminate pause actions on a running run", async () => {
    const { api } = await import("../api");
    vi.mocked(api.getFlowRun).mockResolvedValueOnce({
      id: "run-2",
      name: "live_flow",
      state: "RUNNING",
      version: 1,
      created_at: "2026-04-15T21:00:00+00:00",
      updated_at: "2026-04-15T21:00:01+00:00"
    });
    const queryClient = new QueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/runs/run-2"]}>
          <Routes>
            <Route path="/runs/:id" element={<RunDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );
    expect(await screen.findByRole("heading", { name: "live_flow" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pause (drain)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pause (terminate)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("hides Retry when the run was not created by a deployment", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "mapped_flow" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    expect(
      screen.getByText("This run was not started from a deployment, so it cannot be retried.")
    ).toBeInTheDocument();
  });

  it("retries a failed deployment run in place", async () => {
    const { api } = await import("../api");
    vi.mocked(api.getFlowRun).mockResolvedValue({
      id: "run-failed",
      name: "failed_flow",
      state: "FAILED",
      version: 4,
      deployment_id: "dep-1",
      created_at: "2026-04-15T21:00:00+00:00",
      updated_at: "2026-04-15T21:00:01+00:00"
    });
    vi.mocked(api.retryFlowRun).mockResolvedValue({
      id: "run-failed",
      name: "failed_flow",
      state: "PENDING",
      version: 5,
      deployment_id: "dep-1",
      created_at: "2026-04-15T21:00:00+00:00",
      updated_at: "2026-04-15T21:00:02+00:00"
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/runs/run-failed"]}>
          <Routes>
            <Route path="/runs/:id" element={<RunDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );
    expect(await screen.findByRole("heading", { name: "failed_flow" })).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: "Retry" });
    retry.click();
    expect(await screen.findByText("Retrying this run.")).toBeInTheDocument();
    expect(api.retryFlowRun).toHaveBeenCalledWith("run-failed");
    expect(screen.queryByText("Retry scheduled from deployment.")).not.toBeInTheDocument();
    vi.mocked(api.getFlowRun).mockResolvedValue({
      id: "run-1",
      name: "mapped_flow",
      state: "COMPLETED",
      version: 3,
      created_at: "2026-04-15T21:00:00+00:00",
      updated_at: "2026-04-15T21:00:01+00:00"
    });
  });

  it("labels skipped cache hits on a resume attempt", async () => {
    const { api } = await import("../api");
    vi.mocked(api.getFlowRun).mockResolvedValueOnce({
      id: "run-3",
      name: "resume_flow",
      state: "COMPLETED",
      version: 3,
      created_at: "2026-04-15T21:00:00+00:00",
      updated_at: "2026-04-15T21:00:01+00:00",
      resume_from_flow_run_id: "run-1"
    });
    vi.mocked(api.listFlowArtifacts).mockResolvedValueOnce([
      {
        id: "art-2",
        flow_run_id: "run-3",
        task_run_id: "task-1",
        artifact_type: "result",
        key: "inc-result",
        summary: JSON.stringify({
          task_name: "inc",
          result: 42,
          persisted: true,
          cache_hit: true
        }),
        created_at: "2026-04-15T21:00:01+00:00"
      }
    ]);
    const queryClient = new QueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/runs/run-3"]}>
          <Routes>
            <Route path="/runs/:id" element={<RunDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );
    expect(await screen.findByText(/inc - COMPLETED · skipped/i)).toBeInTheDocument();
    expect(screen.getByText(/Resumed from/i)).toBeInTheDocument();
  });

  it("shows the exception and traceback on a failed task and its log line", async () => {
    const { api } = await import("../api");
    const traceback = [
      "Traceback (most recent call last):",
      '  File "explode.py", line 1, in explode',
      "RuntimeError: intentional failure for DAG/state testing"
    ].join("\n");
    vi.mocked(api.getFlowRun).mockResolvedValueOnce({
      id: "run-fail",
      name: "failing_flow",
      state: "FAILED",
      version: 2,
      created_at: "2026-04-15T21:00:00+00:00",
      updated_at: "2026-04-15T21:00:01+00:00"
    });
    vi.mocked(api.listTaskRuns).mockResolvedValueOnce({
      items: [
        {
          id: "task-fail",
          flow_run_id: "run-fail",
          task_name: "explode",
          state: "FAILED",
          version: 2,
          created_at: "2026-04-15T21:00:00+00:00",
          updated_at: "2026-04-15T21:00:01+00:00",
          error: "intentional failure for DAG/state testing",
          traceback
        }
      ],
      next_cursor: null
    });
    vi.mocked(api.listFlowArtifacts).mockResolvedValueOnce([]);
    vi.mocked(api.listLogs).mockResolvedValueOnce({
      items: [
        {
          id: "log-1",
          flow_run_id: "run-fail",
          task_run_id: "task-fail",
          level: "ERROR",
          message: `explode: task_failed: intentional failure for DAG/state testing\n${traceback}`,
          timestamp: "2026-04-15T21:00:01+00:00"
        }
      ],
      next_cursor: null
    });
    const queryClient = new QueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/runs/run-fail"]}>
          <Routes>
            <Route path="/runs/:id" element={<RunDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );
    expect(await screen.findByText(/explode - FAILED/)).toBeInTheDocument();
    expect(screen.getByText("intentional failure for DAG/state testing")).toBeInTheDocument();
    expect(screen.getByText(/Traceback \(most recent call last\)/)).toBeInTheDocument();
    screen.getByRole("tab", { name: "Logs" }).click();
    expect(
      await screen.findByText(/explode: task_failed: intentional failure for DAG\/state testing/)
    ).toBeInTheDocument();
    expect(screen.getByText(/File "explode.py"/)).toBeInTheDocument();
  });
});
