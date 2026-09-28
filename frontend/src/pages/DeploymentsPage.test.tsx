import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { CRON_WILL_NOT_RUN } from "../schedule/format";
import type { Deployment } from "../types";
import { DeploymentsPage } from "./DeploymentsPage";

vi.mock("../api", () => ({
  api: {
    listDeployments: vi.fn(),
    triggerDeploymentRun: vi.fn()
  }
}));

function deployment(overrides: Partial<Deployment>): Deployment {
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

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <DeploymentsPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("DeploymentsPage schedules", () => {
  it("shows manual deployments with a way to add a schedule", async () => {
    vi.mocked(api.listDeployments).mockResolvedValue({
      items: [deployment({ id: "manual-1", name: "manual-flow" })],
      next_cursor: null
    });
    renderPage();
    expect(await screen.findByText("Manual")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add schedule" })).toHaveAttribute(
      "href",
      "/deployments/manual-1?schedule=edit"
    );
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("shows an interval in words and the next run", async () => {
    vi.mocked(api.listDeployments).mockResolvedValue({
      items: [
        deployment({
          id: "every-hour",
          name: "hourly",
          schedule_enabled: true,
          schedule_interval_seconds: 7200,
          schedule_next_run_at: "2026-09-27T14:00:00Z"
        })
      ],
      next_cursor: null
    });
    renderPage();
    expect(await screen.findByText("Every 2 hours")).toBeInTheDocument();
    expect(screen.getByText(/14:00/)).toBeInTheDocument();
  });

  it("says a cron will not run when the server cannot fire it", async () => {
    vi.mocked(api.listDeployments).mockResolvedValue({
      items: [
        deployment({
          name: "cronned",
          schedule_enabled: true,
          schedule_cron: "0 */10 * * * *",
          schedule_next_run_at: "2026-09-27T12:10:00Z",
          schedule_cron_ticks: false
        })
      ],
      next_cursor: null
    });
    renderPage();
    expect(await screen.findByText("Every 10 minutes")).toBeInTheDocument();
    expect(screen.getByText("Will not run")).toBeInTheDocument();
    expect(screen.getByText(CRON_WILL_NOT_RUN)).toBeInTheDocument();
  });
});