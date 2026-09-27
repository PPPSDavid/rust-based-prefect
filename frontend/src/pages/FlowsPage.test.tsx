import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { FlowsPage } from "./FlowsPage";

vi.mock("../api", () => ({
  api: {
    listFlows: vi.fn(async (cursor?: string) => {
      if (cursor) {
        return {
          items: [
            {
              id: "f2",
              name: "zeta",
              status: "active",
              run_count: 1,
              updated_at: "2026-04-14T21:00:00+00:00"
            }
          ],
          next_cursor: null
        };
      }
      return {
        items: [
          {
            id: "f1",
            name: "alpha",
            status: "active",
            run_count: 3,
            updated_at: "2026-04-15T21:00:00+00:00"
          }
        ],
        next_cursor: "page-2"
      };
    })
  }
}));

function SearchProbe() {
  const [params] = useSearchParams();
  return <div data-testid="qs">{params.toString()}</div>;
}

function renderPage(initialEntry = "/flows") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <FlowsPage />
        <SearchProbe />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("FlowsPage", () => {
  it("renders active catalog flows and archived tab", async () => {
    renderPage();
    expect(await screen.findByText("alpha")).toBeInTheDocument();
    expect(screen.getByText("zeta")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Archived" })).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Search flows" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Sort" })).toBeInTheDocument();
    expect(api.listFlows).toHaveBeenCalledWith("page-2", "active");
  });

  it("shares catalog status, search, and sort in the URL", async () => {
    renderPage();
    expect(await screen.findByText("alpha")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Archived" }));
    expect(screen.getByTestId("qs")).toHaveTextContent("status=archived");
    expect(api.listFlows).toHaveBeenCalledWith(undefined, "archived");
    expect(await screen.findByRole("searchbox", { name: "Search flows" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox", { name: "Search flows" }), { target: { value: "zeta" } });
    expect(screen.getByTestId("qs").textContent).toContain("q=zeta");
    expect(screen.queryByText("alpha")).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "Sort" }), { target: { value: "name:asc" } });
    expect(screen.getByTestId("qs").textContent).toContain("sort=name");
    expect(screen.getByTestId("qs").textContent).toContain("dir=asc");
  });
});
