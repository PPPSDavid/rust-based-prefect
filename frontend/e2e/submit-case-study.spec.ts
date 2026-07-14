/**
 * One-off visual case-study screenshots for deferred-submit E2E review.
 * Run: npx playwright test e2e/submit-case-study.spec.ts
 */
import { test, expect } from "@playwright/test";
import path from "node:path";

const ARTIFACTS = "/opt/cursor/artifacts/submit-e2e";

test("case study: runs list and representative run details", async ({ page }) => {
  await page.goto("/runs");
  await expect(page.getByRole("heading", { name: "Flow Runs" })).toBeVisible();
  await expect(page.getByText("failing_flow").first()).toBeVisible({ timeout: 15000 });
  await expect(page.getByText("wide_flow").first()).toBeVisible();
  await expect(page.getByText("simple_flow").first()).toBeVisible();
  await page.screenshot({ path: path.join(ARTIFACTS, "runs.png"), fullPage: true });

  // Failing flow: Task Runs should show CANCELLED dependent + FAILED explode
  await page.getByRole("link").filter({ hasText: "failing_flow" }).first().click();
  await expect(page.getByText(/FAILED|failed/i).first()).toBeVisible();
  await page.getByRole("tab", { name: "Task Runs" }).click();
  await expect(page.getByText("explode").first()).toBeVisible();
  await expect(page.getByText("after_failure").first()).toBeVisible();
  await expect(page.getByText(/CANCELLED|FAILED|COMPLETED/).first()).toBeVisible();
  await page.screenshot({
    path: path.join(ARTIFACTS, "failing-task-runs.png"),
    fullPage: true
  });

  await page.getByRole("tab", { name: "Events" }).click();
  await expect(page.getByText(/task_pending|task_failed|task_cancelled/).first()).toBeVisible({
    timeout: 10000
  });
  await page.screenshot({
    path: path.join(ARTIFACTS, "failing-events.png"),
    fullPage: true
  });

  await page.getByRole("tab", { name: "DAG" }).click();
  await expect(page.getByRole("button", { name: "Aggregated fan-out" })).toBeVisible();
  await page.screenshot({
    path: path.join(ARTIFACTS, "failing-dag.png"),
    fullPage: true
  });

  // Wide flow DAG
  await page.goto("/runs");
  await page.getByRole("link").filter({ hasText: "wide_flow" }).first().click();
  await page.getByRole("tab", { name: "DAG" }).click();
  await expect(page.getByRole("button", { name: "Task runs" })).toBeVisible();
  await page.getByRole("button", { name: "Task runs" }).click();
  await expect(page.getByText("Loading DAG...")).toBeHidden({ timeout: 15000 });
  await expect(page.getByText(/inc|dbl|source:/).first()).toBeVisible({ timeout: 15000 });
  await page.screenshot({
    path: path.join(ARTIFACTS, "wide-dag-expanded.png"),
    fullPage: true
  });

  // Simple dependency chain
  await page.goto("/runs");
  await page.getByRole("link").filter({ hasText: "simple_flow" }).first().click();
  await page.getByRole("tab", { name: "Task Runs" }).click();
  await expect(page.getByText("inc").first()).toBeVisible();
  await expect(page.getByText("dbl").first()).toBeVisible();
  await page.screenshot({
    path: path.join(ARTIFACTS, "simple-task-runs.png"),
    fullPage: true
  });
});
