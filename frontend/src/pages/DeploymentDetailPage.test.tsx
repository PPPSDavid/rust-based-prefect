import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { CRON_WILL_NOT_RUN } from "../schedule/format";
import type { Deployment } from "../types";
import { DeploymentDetailPage } from "./DeploymentDetailPage";

vi.mock("../api", () => ({
  api: {
    getDeployment: vi.fn(),
    listDeploymentRuns: vi.fn(),
    patchDeployment: vi.fn(),
    triggerDeploymentRun: vi.fn()
  }
}));

function deployment(overrides: Partial<Deployment> = {}): Deployment {
  return {
    id: "dep-1",
    name: "persist",
    flow_name: "persist_result_demo",
    default_parameters: { n: 3 },
    paused: false,
    schedule_enabled: false,
    schedule_cron_ticks: false,
    created_at: "2026-09-27T00:00:00Z",
    updated_at: "2026-09-27T00:00:00Z",
    ...overrides
  };
}

function renderPage(entry = "/deployments/dep-1") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/deployments/:id" element={<DeploymentDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("DeploymentDetailPage schedules", () => {
  beforeEach(() => {
    vi.mocked(api.listDeploymentRuns).mockResolvedValue({ items: [], next_cursor: null });
    vi.mocked(api.patchDeployment).mockResolvedValue(deployment());
  });

  it("lets a manual deployment add an interval schedule", async () => {
    vi.mocked(api.getDeployment).mockResolvedValue(deployment());
    renderPage();
    expect(await screen.findByText("Manual")).toBeInTheDocument();
    expect(screen.getByText("Runs only when you start it.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add schedule" }));
    expect(screen.getByText("Every hour")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save schedule" }));
    await waitFor(() =>
      expect(api.patchDeployment).toHaveBeenCalledWith("dep-1", {
        schedule_enabled: true,
        schedule_interval_seconds: 3600,
        schedule_cron: null,
        schedule_rrule: null,
        schedule_next_run_at: null
      })
    );
  });

  it("warns that a cron will not run on the Python scheduler", async () => {
    vi.mocked(api.getDeployment).mockResolvedValue(
      deployment({
        schedule_enabled: true,
        schedule_cron: "0 0 9 * * *",
        schedule_next_run_at: "2026-09-28T09:00:00Z",
        schedule_cron_ticks: false
      })
    );
    renderPage();
    expect(await screen.findByText("Every day at 09:00 UTC")).toBeInTheDocument();
    expect(screen.getByText("Next run: Will not run")).toBeInTheDocument();
    expect(screen.getByText(CRON_WILL_NOT_RUN)).toBeInTheDocument();
  });

  it("shows the next run when cron ticks are active", async () => {
    vi.mocked(api.getDeployment).mockResolvedValue(
      deployment({
        schedule_enabled: true,
        schedule_cron: "@daily",
        schedule_next_run_at: "2026-09-28T00:00:00Z",
        schedule_cron_ticks: true
      })
    );
    renderPage();
    expect(await screen.findByText("Every day at 00:00 UTC")).toBeInTheDocument();
    expect(screen.getByText(/Next run:/)).toHaveTextContent("00:00");
    expect(screen.queryByText(CRON_WILL_NOT_RUN)).not.toBeInTheDocument();
  });
});