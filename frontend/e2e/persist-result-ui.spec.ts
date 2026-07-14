import { test, expect } from "@playwright/test";
import path from "node:path";

const API = "http://127.0.0.1:8000";
const ARTIFACTS_DIR = "/opt/cursor/artifacts";

test.describe("persist result UI", () => {
  test("Task Runs and Artifacts tabs show JSON-safe persisted results", async ({
    page,
    request,
  }) => {
    // Seed against the live server (same pattern as gate-task-dag.spec.ts).
    const seeded = await request.post(`${API}/benchmark/run`, {
      data: { flavor: "persist_result", complexity: 7 },
    });
    expect(seeded.ok()).toBeTruthy();

    const runs = await request.get(`${API}/api/flow-runs?limit=50`);
    expect(runs.ok()).toBeTruthy();
    const items = (await runs.json()).items as Array<{ id: string; name: string; state: string }>;
    const demo = items.find((r) => r.name === "persist_result_demo" && r.state === "COMPLETED");
    expect(demo, "persist_result_demo flow run must exist after seed").toBeTruthy();

    await page.goto(`/runs/${demo!.id}`);
    await expect(page.getByRole("heading", { name: "persist_result_demo" })).toBeVisible();

    // Task Runs tab (default): persisted payload for expensive
    await expect(page.getByText("expensive - COMPLETED")).toBeVisible();
    await expect(page.locator("pre.task-result").filter({ hasText: '"n": 42' })).toBeVisible();
    await expect(page.locator("pre.task-result").filter({ hasText: '"x": 7' })).toBeVisible();
    // None result shows as null
    await expect(page.getByText("setup - COMPLETED")).toBeVisible();
    await expect(page.locator("pre.task-result").filter({ hasText: "null" }).first()).toBeVisible();

    await page.screenshot({
      path: path.join(ARTIFACTS_DIR, "persist-result-task-runs.png"),
      fullPage: true,
    });

    await page.getByRole("tab", { name: "Artifacts" }).click();
    await expect(page.getByText("expensive-result")).toBeVisible();
    await expect(page.locator("pre.task-result").filter({ hasText: '"n": 42' })).toBeVisible();

    await page.screenshot({
      path: path.join(ARTIFACTS_DIR, "persist-result-artifacts.png"),
      fullPage: true,
    });
  });
});
