import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { RunsPage } from "./RunsPage";

vi.mock("../hooks/useSsePulse", () => ({
  useSsePulse: () => 0
}));

const completed = {
  id: "run-1",
  name: "mapped_flow",
  flow_name: "mapped_flow",
  state: "COMPLETED",
  version: 3,
  created_at: "2026-04-15T21:00:00.000Z",
  updated_at: "2026-04-15T21:00:02.000Z",
  deployment_id: "dep-1",
  deployment_name: "mapped-local",
  tags: ["nightly"],
  start_time: "2026-04-15T21:00:00.000Z",
  end_time: "2026-04-15T21:00:01.500Z"
};

vi.mock("../api", () => ({
  api: {
    streamFlowRuns: vi.fn(),
    listFlowRuns: vi.fn(async (params?: { state?: string; q?: string; cursor?: string }) => {
      if (params?.state === "FAILED") {
        return {
          items: [
            {
              id: "run-failed",
              name: "failing_flow",
              flow_name: "failing_flow",
              state: "FAILED",
              version: 1,
              created_at: "2026-04-15T21:00:00.000Z",
              updated_at: "2026-04-15T21:00:01.000Z",
              tags: [],
              start_time: null,
              end_time: null
            }
          ],
          next_cursor: null
        };
      }
      return { items: [completed], next_cursor: params?.cursor ? null : "9" };
    })
  }
}));

function renderPage(entry = "/runs") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <RunsPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("RunsPage", () => {
  beforeEach(() => {
    vi.mocked(api.listFlowRuns).mockClear();
  });

  it("renders flow, deployment, tags, and deployment duration", async () => {
    renderPage();
    expect(await screen.findByRole("link", { name: "mapped-local" })).toHaveAttribute(
      "href",
      "/deployments/dep-1"
    );
    const flowLinks = screen.getAllByRole("link", { name: "mapped_flow" });
    expect(flowLinks.some((link) => link.getAttribute("href") === "/flows/mapped_flow")).toBe(true);
    expect(screen.getByText("nightly")).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "1.5s" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "PAUSED" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "SCHEDULED" })).toBeInTheDocument();
  });

  it("sends search to the server instead of hiding loaded rows", async () => {
    renderPage();
    expect(await screen.findByRole("link", { name: "mapped-local" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox", { name: "Search runs" }), {
      target: { value: "zzz" }
    });
    await waitFor(() => {
      expect(api.listFlowRuns).toHaveBeenCalledWith(expect.objectContaining({ q: "zzz" }));
    });
    expect(await screen.findByRole("link", { name: "mapped-local" })).toBeInTheDocument();
  });

  it("opens the Failed view from the URL", async () => {
    renderPage("/runs?view=failed");
    expect((await screen.findAllByRole("link", { name: "failing_flow" })).length).toBeGreaterThan(0);
    expect(api.listFlowRuns).toHaveBeenCalledWith(expect.objectContaining({ state: "FAILED" }));
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("requests the next cursor when loading more", async () => {
    renderPage();
    expect(await screen.findByRole("link", { name: "mapped-local" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => {
      expect(api.listFlowRuns).toHaveBeenCalledWith(expect.objectContaining({ cursor: "9" }));
    });
  });
});
