import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { RunDagPanel } from "./RunDagPanel";

vi.mock("../api", () => ({
  api: {
    getFlowRunDag: vi.fn()
  }
}));

const baseDag = {
  flow_run_id: "parent-run",
  mode: "logical" as const,
  source: "runtime",
  fallback_required: false,
  warnings: [],
  forecast: {},
  nodes: [
    { id: "n1", label: "task-a", state: "COMPLETED", kind: "task" as const },
    {
      id: "inline:child-run",
      label: "child_flow",
      state: "COMPLETED",
      kind: "inline_subflow" as const,
      child_flow_run_id: "child-run",
      execution_mode: "inline"
    },
    {
      id: "n2",
      label: "child-deploy",
      state: "COMPLETED",
      kind: "subflow_task" as const,
      child_flow_run_id: "dep-child-run"
    }
  ],
  edges: [
    { from: "n1", to: "inline:child-run" },
    { from: "inline:child-run", to: "n2" }
  ]
};

function renderPanel() {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <RunDagPanel dag={baseDag} mode="logical" onModeChange={() => undefined} />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("RunDagPanel subflow nodes", () => {
  it("renders subflow legend entries", () => {
    renderPanel();
    expect(screen.getAllByText(/inline subflow/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/deployment subflow/i).length).toBeGreaterThan(0);
  });

  it("starts inline subflow expansion on click", async () => {
    vi.mocked(api.getFlowRunDag).mockResolvedValue({
      flow_run_id: "child-run",
      mode: "logical",
      source: "runtime",
      fallback_required: true,
      warnings: [],
      forecast: {},
      nodes: [{ id: "c1", label: "inc", state: "COMPLETED", kind: "task" }],
      edges: []
    });
    renderPanel();
    const inlineNode = document.querySelector(".dag-node-inline-subflow");
    expect(inlineNode).not.toBeNull();
    fireEvent.click(inlineNode!);
    expect(await screen.findByText(/Inline subflow: child_flow/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open run" })).toBeInTheDocument();
  });
});
