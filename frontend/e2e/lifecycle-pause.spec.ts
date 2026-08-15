import { test, expect } from "@playwright/test";

const API = "http://127.0.0.1:8000";

async function waitForDeploymentFlowRun(
  request: import("@playwright/test").APIRequestContext,
  deploymentRunId: string,
  desiredState: string,
  timeoutMs = 60_000
) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const depRuns = await request.get(`${API}/api/deployment-runs?limit=30`);
    expect(depRuns.ok()).toBeTruthy();
    const depRun = ((await depRuns.json()).items as Array<{ id: string; flow_run_id: string | null }>).find(
      (row) => row.id === deploymentRunId
    );
    if (depRun?.flow_run_id) {
      const flowRes = await request.get(`${API}/api/flow-runs/${depRun.flow_run_id}`);
      expect(flowRes.ok()).toBeTruthy();
      const flowRun = await flowRes.json();
      if (flowRun.state === desiredState) {
        return { flowRunId: depRun.flow_run_id as string, flowRun };
      }
    }
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error(`Timed out waiting for deployment run ${deploymentRunId} to reach ${desiredState}`);
}

async function triggerCancelable(request: import("@playwright/test").APIRequestContext, sleepDuration: number) {
  const deps = await request.get(`${API}/api/deployments?limit=50`);
  expect(deps.ok()).toBeTruthy();
  const dep = ((await deps.json()).items as Array<{ id: string; name: string }>).find(
    (d) => d.name === "cancelable_flow-local"
  );
  expect(dep, "cancelable_flow-local deployment must exist").toBeTruthy();
  const triggered = await request.post(`${API}/api/deployments/${dep!.id}/run`, {
    data: { parameters: { n: 1, sleep_duration: sleepDuration } }
  });
  expect(triggered.ok()).toBeTruthy();
  return (await triggered.json()).id as string;
}

test.describe("lifecycle pause chooser", () => {
  test.setTimeout(120_000);

  test("pause drain chooser, badges, resume", async ({ page, request }) => {
    const deploymentRunId = await triggerCancelable(request, 6);
    const running = await waitForDeploymentFlowRun(request, deploymentRunId, "RUNNING", 30_000);

    await page.goto(`/runs/${running.flowRunId}`);
    await expect(page.getByRole("heading", { name: "cancelable_flow" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Pause (drain)" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Pause (terminate)" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Cancel" })).toBeVisible();
    await expect(page.getByText("Parameters")).toBeVisible();

    await page.getByRole("button", { name: "Pause (drain)" }).click();
    await expect(page.getByText("Pause (drain) requested.")).toBeVisible();

    await expect(page.getByText(/operator pause/i)).toBeVisible({ timeout: 30_000 });
    await expect(page.locator(".badge-paused")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("button", { name: "Resume" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Pause (drain)" })).toHaveCount(0);

    await page.getByRole("button", { name: "Resume" }).click();
    await expect(page.getByText("Run resumed.")).toBeVisible();
  });

  test("pause terminate chooser and resume retry", async ({ page, request }) => {
    const deploymentRunId = await triggerCancelable(request, 12);
    const running = await waitForDeploymentFlowRun(request, deploymentRunId, "RUNNING", 30_000);

    await page.goto(`/runs/${running.flowRunId}`);
    await page.getByRole("button", { name: "Pause (terminate)" }).click();
    await expect(page.getByText("Pause (terminate) requested.")).toBeVisible();
    await expect(page.getByText(/operator pause/i)).toBeVisible({ timeout: 15_000 });
    await expect(page.locator(".badge-paused")).toBeVisible({ timeout: 15_000 });

    await page.getByRole("tab", { name: "Logs" }).click();
    await expect(page.getByRole("searchbox", { name: /Search/i })).toBeVisible();
    await expect(page.getByRole("combobox", { name: /Level/i })).toBeVisible();
    await expect(page.getByRole("combobox", { name: /Task/i })).toBeVisible();

    await page.getByRole("button", { name: "Resume" }).click();
    await expect(page.getByText(/Resume scheduled a new deployment attempt|Run resumed/)).toBeVisible();
  });
});
