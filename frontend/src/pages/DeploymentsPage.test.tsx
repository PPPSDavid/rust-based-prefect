import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { DeploymentsPage } from "./DeploymentsPage";

vi.mock("../api", () => ({
  api: {
    listDeployments: vi.fn(async (cursor?: string) => {
      const base = {
        default_parameters: {},
        paused: false,
        created_at: "2026-04-15T21:00:00+00:00",
        updated_at: "2026-04-15T21:00:00+00:00",
        schedule_enabled: false
      };
      if (cursor) {
        return {
          items: [{ ...base, id: "d3", name: "only-one", flow_name: "solo" }],
          next_cursor: null
        };
      }
      return {
        items: [
          {
            ...base,
            id: "d1",
            name: "hourly",
            flow_name: "etl",
            schedule_enabled: true,
            schedule_cron: "0 * * * *"
          },
          {
            ...base,
            id: "d2",
            name: "nightly",
            flow_name: "etl",
            paused: true,
            schedule_enabled: true,
            schedule_interval_seconds: 3600
          }
        ],
        next_cursor: "page-2"
      };
    }),
    triggerDeploymentRun: vi.fn()
  }
}));

function SearchProbe() {
  const [params] = useSearchParams();
  return <div data-testid="qs">{params.toString()}</div>;
}

function renderPage(initialEntry = "/deployments") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <DeploymentsPage />
        <SearchProbe />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("DeploymentsPage", () => {
  it("groups deployments under their flow and keeps name, schedule, and status", async () => {
    renderPage();
    expect(await screen.findByRole("link", { name: "hourly" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "nightly" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "only-one" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "etl" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "solo" })).toBeInTheDocument();
    expect(screen.getByText("2 deployments")).toBeInTheDocument();
    expect(screen.getByText("1 deployment")).toBeInTheDocument();
    expect(screen.getByText("cron 0 * * * *")).toBeInTheDocument();
    expect(screen.getByText("every 3600s")).toBeInTheDocument();
    expect(screen.getByText("Manual")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "only-one" }).closest("td")).toHaveClass("list-group-child");
    const nightlyRow = screen.getByRole("link", { name: "nightly" }).closest("tr");
    expect(nightlyRow).not.toBeNull();
    expect(within(nightlyRow as HTMLElement).getByText("Paused")).toBeInTheDocument();
    expect(within(nightlyRow as HTMLElement).getByRole("button", { name: "Quick Run" })).toBeDisabled();
    expect(api.listDeployments).toHaveBeenCalledWith("page-2");
  });

  it("collapses a flow group without removing the flow name", async () => {
    renderPage();
    expect(await screen.findByRole("link", { name: "only-one" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Toggle deployments for solo" }));
    expect(screen.queryByRole("link", { name: "only-one" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "solo" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Toggle deployments for solo" })).toHaveAttribute(
      "aria-expanded",
      "false"
    );
  });

  it("writes status, search, and sort into the URL", async () => {
    renderPage();
    expect(await screen.findByRole("link", { name: "hourly" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Paused" }));
    expect(screen.getByTestId("qs")).toHaveTextContent("status=paused");
    expect(screen.getByRole("link", { name: "nightly" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "hourly" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox", { name: "Search deployments" }), {
      target: { value: "missing" }
    });
    expect(screen.getByTestId("qs").textContent).toContain("q=missing");
    expect(screen.getByText("No deployments match this view.")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "Sort" }), { target: { value: "flow:desc" } });
    expect(screen.getByTestId("qs").textContent).toContain("sort=flow");
    expect(screen.getByTestId("qs").textContent).toContain("dir=desc");
  });

  it("opens a shared search from the URL", async () => {
    renderPage("/deployments?q=hourly");
    expect(await screen.findByRole("link", { name: "hourly" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "nightly" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "only-one" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "etl" })).toBeInTheDocument();
    expect(screen.getByText("cron 0 * * * *")).toBeInTheDocument();
  });
});
