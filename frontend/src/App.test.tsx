import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";

vi.mock("./hooks/useSsePulse", () => ({
  useSsePulse: () => 0
}));

vi.mock("./api", () => ({
  api: {
    streamFlowRuns: vi.fn(),
    listFlowRuns: vi.fn().mockResolvedValue({ items: [], next_cursor: null })
  }
}));

describe("App navigation", () => {
  it("renders primary nav links", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } }
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/runs"]}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>
    );
    expect(await screen.findByRole("link", { name: "Flow Runs" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Deployments" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Work Pools" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Concurrency" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Dashboard" })).toBeInTheDocument();
  });
});
