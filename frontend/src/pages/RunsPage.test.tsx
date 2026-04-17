import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { RunsPage } from "./RunsPage";

vi.mock("../hooks/useSsePulse", () => ({
  useSsePulse: () => 0
}));

vi.mock("../api", () => ({
  api: {
    streamFlowRuns: vi.fn(),
    listFlowRuns: vi.fn().mockResolvedValue({
      items: [
        {
          id: "run-1",
          name: "mapped_flow",
          state: "COMPLETED",
          version: 3,
          created_at: "2026-04-15T21:00:00+00:00",
          updated_at: "2026-04-15T21:00:01+00:00"
        }
      ],
      next_cursor: null
    })
  }
}));

function renderPage() {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <RunsPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("RunsPage", () => {
  it("renders flow runs from API", async () => {
    renderPage();
    expect(await screen.findByText("mapped_flow")).toBeInTheDocument();
    expect(screen.getByText("COMPLETED")).toBeInTheDocument();
  });
});
