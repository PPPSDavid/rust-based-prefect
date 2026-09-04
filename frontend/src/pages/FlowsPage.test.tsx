import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { FlowsPage } from "./FlowsPage";

vi.mock("../api", () => ({
  api: {
    listFlows: vi.fn().mockResolvedValue({
      items: [
        {
          id: "f1",
          name: "alpha",
          status: "active",
          run_count: 3,
          updated_at: "2026-04-15T21:00:00+00:00"
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
        <FlowsPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("FlowsPage", () => {
  it("renders active catalog flows and archived tab", async () => {
    renderPage();
    expect(await screen.findByText("alpha")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Archived" })).toBeInTheDocument();
  });
});
