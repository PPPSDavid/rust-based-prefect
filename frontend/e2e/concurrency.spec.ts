import { test, expect } from "@playwright/test";

test("lists a concurrency limit created via the API", async ({ page, request }) => {
  const name = `ui-gcl-${Date.now()}`;
  const res = await request.post("http://127.0.0.1:8000/api/concurrency-limits", {
    data: { name, limit: 3, slot_decay_per_second: 1.25 }
  });
  expect(res.ok()).toBeTruthy();

  await page.goto("/concurrency");
  await expect(page.getByRole("heading", { name: "Concurrency" })).toBeVisible();
  await expect(page.getByRole("button", { name })).toBeVisible();
  await page.getByRole("button", { name }).click();
  await expect(page.getByLabel("Limit inspect payload")).toContainText(`"name": "${name}"`);
});
