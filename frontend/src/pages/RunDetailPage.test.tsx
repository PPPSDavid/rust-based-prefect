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
    listFlowArtifacts: vi.fn().mockResolvedValue([]),
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
    expect(await screen.findByText("mapped_flow")).toBeInTheDocument();
    const dagButton = screen.getByRole("button", { name: "DAG" });
    dagButton.click();
    expect(await screen.findByText(/source:/i)).toBeInTheDocument();
    expect(await screen.findByText("inc")).toBeInTheDocument();
  });
});
