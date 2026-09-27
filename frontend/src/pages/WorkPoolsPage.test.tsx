import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { WorkPoolsPage } from "./WorkPoolsPage";

vi.mock("../api", () => ({
  api: {
    listWorkPools: vi.fn(async (cursor?: string) => {
      if (cursor) {
        return {
          items: [
            {
              id: "p2",
              name: "zeta",
              type: "process",
              paused: true,
              created_at: "2026-04-14T21:00:00+00:00",
              updated_at: "2026-04-14T21:00:00+00:00"
            }
          ],
          next_cursor: null
        };
      }
      return {
        items: [
          {
            id: "p1",
            name: "alpha",
            type: "process",
            paused: false,
            created_at: "2026-04-15T21:00:00+00:00",
            updated_at: "2026-04-16T21:00:00+00:00"
          }
        ],
        next_cursor: "page-2"
      };
    }),
    createWorkPool: vi.fn()
  }
}));

function SearchProbe() {
  const [params] = useSearchParams();
  return <div data-testid="qs">{params.toString()}</div>;
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <WorkPoolsPage />
        <SearchProbe />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("WorkPoolsPage", () => {
  it("loads later pages and shares search, status, and sort", async () => {
    renderPage();
    expect(await screen.findByRole("link", { name: "alpha" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "zeta" })).toBeInTheDocument();
    expect(api.listWorkPools).toHaveBeenCalledWith("page-2");
    fireEvent.click(screen.getByRole("button", { name: "Paused" }));
    expect(screen.getByTestId("qs")).toHaveTextContent("status=paused");
    expect(screen.queryByRole("link", { name: "alpha" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "zeta" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "Sort" }), { target: { value: "name:asc" } });
    expect(screen.getByTestId("qs").textContent).toContain("sort=name");
    expect(screen.getByTestId("qs").textContent).toContain("dir=asc");
    expect(screen.getByRole("button", { name: "Create pool" })).toBeInTheDocument();
  });
});
