import { test, expect } from "@playwright/test";

test("shows default process work pool", async ({ page }) => {
  await page.goto("/work-pools");
  await expect(page.getByText("default-process-pool")).toBeVisible();
});
