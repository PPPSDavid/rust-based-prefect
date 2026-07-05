import { test, expect } from "@playwright/test";

test.beforeAll(async ({ request }) => {
  const res = await request.post("http://127.0.0.1:8000/benchmark/run", {
    data: { flavor: "simple", complexity: 4 }
  });
  expect(res.ok()).toBeTruthy();
});

test("navigates primary sections", async ({ page }) => {
  await page.goto("/runs");
  await expect(page.getByRole("heading", { name: "Flow Runs" })).toBeVisible();
  await page.getByRole("link", { name: "Deployments" }).click();
  await expect(page.getByRole("heading", { name: "Deployments" })).toBeVisible();
  await page.getByRole("link", { name: "Flows" }).click();
  await expect(page.getByRole("heading", { name: "Flows" })).toBeVisible();
  await page.getByRole("link", { name: "Work Pools" }).click();
  await expect(page.getByRole("heading", { name: "Work Pools" })).toBeVisible();
});

test("opens run detail tabs", async ({ page }) => {
  await page.goto("/runs");
  await page
    .getByRole("link")
    .filter({ hasText: /simple_flow|wide_flow|long_chain_flow|mapped_flow|chained_flow/ })
    .first()
    .click();
  await page.getByRole("tab", { name: "DAG" }).click();
  await expect(page.getByText(/logical|expanded/i)).toBeVisible();
});
