import { test, expect } from "@playwright/test";

test("deployment quick run modal accepts parameters", async ({ page }) => {
  await page.goto("/deployments");
  await page.getByRole("button", { name: "Quick Run" }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "Run", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});
