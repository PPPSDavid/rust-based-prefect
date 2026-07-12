import { test, expect } from "@playwright/test";

test("gate task node renders in run DAG", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8000/benchmark/run", {
    data: { flavor: "gated", complexity: 2 },
  });
  const runs = await request.get("http://127.0.0.1:8000/api/flow-runs?limit=5");
  expect(runs.ok()).toBeTruthy();
  const body = await runs.json();
  const gated = (body.items as Array<{ id: string; name: string }>).find(
    (item) => item.name === "gated_flow",
  );
  expect(gated).toBeTruthy();

  await page.goto(`http://localhost:4173/runs/${gated!.id}?tab=dag`);
  await expect(page.getByText(/temporal gate/i)).toBeVisible({ timeout: 15000 });
  await expect(page.locator(".dag-node-gate-task")).toHaveCount(1, { timeout: 15000 });
  await page.screenshot({
    path: "/opt/cursor/artifacts/gate-task-dag.png",
    fullPage: true,
  });
});
