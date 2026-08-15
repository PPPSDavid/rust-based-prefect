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

test.describe("cancel and retry workflow", () => {
  test.setTimeout(120_000);

  test("quick run, cancel while sleeping, retry, wait for completion", async ({ page, request }) => {
    const deps = await request.get(`${API}/api/deployments?limit=50`);
    expect(deps.ok()).toBeTruthy();
    const dep = ((await deps.json()).items as Array<{ id: string; name: string }>).find(
      (d) => d.name === "cancelable_flow-local"
    );
    expect(dep, "cancelable_flow-local deployment must exist").toBeTruthy();

    const triggered = await request.post(`${API}/api/deployments/${dep!.id}/run`, {
      data: { parameters: { n: 1, sleep_duration: 8 } }
    });
    expect(triggered.ok()).toBeTruthy();
    const deploymentRunId = (await triggered.json()).id as string;

    const running = await waitForDeploymentFlowRun(request, deploymentRunId, "RUNNING", 30_000);

    await page.goto(`/runs/${running.flowRunId}`);
    await expect(page.getByRole("heading", { name: "cancelable_flow" })).toBeVisible();
    await expect(page.locator(".badge-running")).toBeVisible();

    const cancelButton = page.getByRole("button", { name: "Cancel", exact: true });
    await expect(cancelButton).toBeVisible();
    await cancelButton.click();

    await expect(page.locator(".badge-cancelled")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("Run cancelled (terminate).")).toBeVisible();

    const tasksBeforeRetry = await request.get(`${API}/api/flow-runs/${running.flowRunId}/task-runs?limit=20`);
    const taskItems = (await tasksBeforeRetry.json()).items as Array<{ task_name: string; state: string }>;
    expect(taskItems.find((t) => t.task_name === "inc")?.state).toBe("COMPLETED");
    expect(taskItems.find((t) => t.task_name === "sleep_seconds")?.state).toBe("CANCELLED");
    expect(taskItems.some((t) => t.task_name === "dbl" && t.state === "COMPLETED")).toBeFalsy();

    const retryButton = page.getByRole("button", { name: "Retry", exact: true });
    await expect(retryButton).toBeVisible();
    const retryResponse = page.waitForResponse(
      (response) => response.url().includes("/retry") && response.request().method() === "POST"
    );
    await retryButton.click();
    const retryResult = await retryResponse;
    expect(retryResult.ok()).toBeTruthy();
    await expect(page.getByText("Retry scheduled from deployment.")).toBeVisible();

    const retryDeploymentRunId = (await retryResult.json()).id as string;
    const completed = await waitForDeploymentFlowRun(request, retryDeploymentRunId, "COMPLETED", 60_000);

    await page.goto(`/runs/${completed.flowRunId}`);
    await expect(page.locator(".badge-completed")).toBeVisible({ timeout: 15_000 });

    const tasksAfterRetry = await request.get(`${API}/api/flow-runs/${completed.flowRunId}/task-runs?limit=20`);
    const retryTasks = (await tasksAfterRetry.json()).items as Array<{ task_name: string; state: string }>;
    expect(retryTasks.find((t) => t.task_name === "inc")?.state).toBe("COMPLETED");
    expect(retryTasks.find((t) => t.task_name === "sleep_seconds")?.state).toBe("COMPLETED");
    expect(retryTasks.find((t) => t.task_name === "dbl")?.state).toBe("COMPLETED");
  });
});
