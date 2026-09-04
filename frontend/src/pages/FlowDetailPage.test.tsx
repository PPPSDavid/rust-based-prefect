import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { FlowDetailPage } from "./FlowDetailPage";

vi.mock("../api", () => ({
  api: {
    getFlow: vi.fn().mockResolvedValue({
      id: "f1",
      name: "beta",
      status: "active",
      aliases: ["alpha"],
      canonical_name: "beta",
      resolved_from_alias: true,
      requested_name: "alpha",
      tasks: [],
      deployments: [],
      recent_runs: []
    })
  }
}));

function renderPage() {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/flows/alpha"]}>
        <Routes>
          <Route path="/flows/:name" element={<FlowDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("FlowDetailPage", () => {
  it("shows alias banner and lifecycle actions", async () => {
    renderPage();
    expect(await screen.findByText("beta")).toBeInTheDocument();
    expect(screen.getByText(/is now an alias of/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Rename" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Archive" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });
});
