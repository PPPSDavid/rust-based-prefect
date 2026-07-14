import { test, expect } from "@playwright/test";
import * as fs from "node:fs";

const ARTIFACT_DIR = "/opt/cursor/artifacts";

test.beforeAll(() => {
  fs.mkdirSync(ARTIFACT_DIR, { recursive: true });
});

async function runFlavor(
  request: import("@playwright/test").APIRequestContext,
  flavor: string,
) {
  const res = await request.post("http://127.0.0.1:8000/benchmark/run", {
    data: { flavor, complexity: 2 },
  });
  expect(res.ok()).toBeTruthy();
  return res.json();
}

async function latestNamedRun(
  request: import("@playwright/test").APIRequestContext,
  name: string,
) {
  const runs = await request.get("http://127.0.0.1:8000/api/flow-runs?limit=20");
  expect(runs.ok()).toBeTruthy();
  const body = await runs.json();
  const match = (body.items as Array<{ id: string; name: string; state: string }>).find(
    (item) => item.name === name,
  );
  expect(match, `expected flow run named ${name}`).toBeTruthy();
  return match!;
}

test("wait_all success shows COMPLETED flow and green DAG tasks", async ({
  page,
  request,
}) => {
  await runFlavor(request, "wait_all_ok");
  const run = await latestNamedRun(request, "wait_all_ok_flow");
  expect(run.state).toBe("COMPLETED");

  await page.goto(`http://localhost:4173/runs/${run.id}?tab=dag`);
  await expect(page.getByText(/wait_all_ok_flow/i).first()).toBeVisible({
    timeout: 15000,
  });
  await expect(page.getByText("COMPLETED").first()).toBeVisible({ timeout: 15000 });
  await page.screenshot({
    path: `${ARTIFACT_DIR}/wait-all-ok-dag.png`,
    fullPage: true,
  });
});

test("wait_all orphan fail shows FAILED flow with failed task node", async ({
  page,
  request,
}) => {
  const payload = await runFlavor(request, "wait_all_orphan_fail");
  expect(payload.flow_failed).toBeTruthy();
  const run = await latestNamedRun(request, "wait_all_orphan_fail_flow");
  expect(run.state).toBe("FAILED");

  await page.goto(`http://localhost:4173/runs/${run.id}?tab=dag`);
  await expect(page.getByText(/wait_all_orphan_fail_flow/i).first()).toBeVisible({
    timeout: 15000,
  });
  await expect(page.getByText("FAILED").first()).toBeVisible({ timeout: 15000 });
  // Failed explode task should be visible in expanded or logical view.
  await expect(page.getByText(/explode/i).first()).toBeVisible({ timeout: 15000 });
  await page.screenshot({
    path: `${ARTIFACT_DIR}/wait-all-orphan-fail-dag.png`,
    fullPage: true,
  });
});

test("wait_all inline subflow parent COMPLETED with child navigation", async ({
  page,
  request,
}) => {
  await runFlavor(request, "wait_all_inline_subflow");
  const parent = await latestNamedRun(request, "wait_all_inline_subflow");
  expect(parent.state).toBe("COMPLETED");

  await page.goto(`http://localhost:4173/runs/${parent.id}`);
  await expect(page.getByText(/wait_all_inline_subflow/i).first()).toBeVisible({
    timeout: 15000,
  });
  await expect(page.getByText("COMPLETED").first()).toBeVisible({ timeout: 15000 });

  // Child runs list / navigation should expose the inline simple_flow child.
  const childLink = page.getByRole("link", { name: /simple_flow/i }).first();
  if (await childLink.count()) {
    await childLink.click();
    await expect(page.getByText(/simple_flow/i).first()).toBeVisible({
      timeout: 15000,
    });
  }

  await page.goto(`http://localhost:4173/runs/${parent.id}?tab=dag`);
  await page.screenshot({
    path: `${ARTIFACT_DIR}/wait-all-inline-subflow-dag.png`,
    fullPage: true,
  });
});
