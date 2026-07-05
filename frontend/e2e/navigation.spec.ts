import { test, expect } from "@playwright/test";

test.beforeAll(async ({ request }) => {
  await request.post("http://127.0.0.1:8000/benchmark/run", {
    data: { flavor: "mapped", complexity: 4 }
  });
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
  await page.getByRole("link").filter({ hasText: /mapped_flow|chained_flow|simple_flow/ }).first().click();
  await page.getByRole("tab", { name: "DAG" }).click();
  await expect(page.getByRole("button", { name: "Logical" })).toBeVisible();
});
