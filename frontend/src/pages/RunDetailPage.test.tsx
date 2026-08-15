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
});
