import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { ConcurrencyPage } from "./ConcurrencyPage";

const sampleLimit = {
  id: "lim-1",
  name: "db",
  limit: 5,
  active_slots: 1,
  slot_decay_per_second: 1.5,
  active: true,
  created_at: "2026-08-15T00:00:00+00:00",
  updated_at: "2026-08-15T00:00:01+00:00"
};

vi.mock("../api", () => ({
  api: {
    listConcurrencyLimits: vi.fn(),
    upsertConcurrencyLimit: vi.fn(),
    patchConcurrencyLimit: vi.fn(),
    deleteConcurrencyLimit: vi.fn()
  }
}));

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } }
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ConcurrencyPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("ConcurrencyPage", () => {
  beforeEach(() => {
    vi.mocked(api.listConcurrencyLimits).mockResolvedValue({ limits: [sampleLimit] });
    vi.mocked(api.upsertConcurrencyLimit).mockResolvedValue(sampleLimit);
    vi.mocked(api.patchConcurrencyLimit).mockResolvedValue({ ...sampleLimit, active: false });
    vi.mocked(api.deleteConcurrencyLimit).mockResolvedValue({ ok: true, deleted: true });
  });

  it("renders limits from the API", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "Concurrency" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "db" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "5" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "1" })).toBeInTheDocument();
  });

  it("creates a limit from the toolbar", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "Concurrency" });
    fireEvent.change(screen.getByLabelText("Limit name"), { target: { value: "api" } });
    fireEvent.change(screen.getByLabelText("Slot limit"), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("Slot decay per second"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Create limit" }));
    await waitFor(() =>
      expect(api.upsertConcurrencyLimit).toHaveBeenCalledWith({
        name: "api",
        limit: 10,
        slot_decay_per_second: 2
      })
    );
  });

  it("inspects, deactivates, and deletes a limit", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "db" }));
    expect(await screen.findByLabelText("Limit inspect payload")).toHaveTextContent(/"active_slots": 1/);
    fireEvent.click(screen.getByRole("button", { name: "Deactivate" }));
    await waitFor(() =>
      expect(api.patchConcurrencyLimit).toHaveBeenCalledWith("db", { active: false })
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(api.deleteConcurrencyLimit).toHaveBeenCalledWith("db"));
  });
});
