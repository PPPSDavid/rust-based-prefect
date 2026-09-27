import { describe, expect, it } from "vitest";
import { followNewFlowRun, idempotencyReplayNote } from "./followNewFlowRun";
import type { DeploymentRun } from "./types";

function deploymentRun(partial: Partial<DeploymentRun>): DeploymentRun {
  return {
    id: "dep-run-1",
    deployment_id: "dep-1",
    status: "SCHEDULED",
    requested_parameters: {},
    resolved_parameters: {},
    created_at: "2026-04-15T21:00:00+00:00",
    updated_at: "2026-04-15T21:00:00+00:00",
    ...partial
  };
}

describe("followNewFlowRun", () => {
  it("opens an already-bound flow run and notes an idempotency replay", async () => {
    const outcome = await followNewFlowRun(
      deploymentRun({ flow_run_id: "flow-9" }),
      "same-key",
      async () => null
    );
    expect(outcome).toEqual({
      kind: "open",
      flowRunId: "flow-9",
      idempotencyReplay: true
    });
    expect(idempotencyReplayNote({ idempotencyReplay: true })).toBe(
      "This idempotency key was already used."
    );
  });

  it("polls until the new flow run id appears", async () => {
    const outcome = await followNewFlowRun(
      deploymentRun({}),
      undefined,
      async () => deploymentRun({ status: "RUNNING", flow_run_id: "flow-new" }),
      1_000
    );
    expect(outcome).toEqual({
      kind: "open",
      flowRunId: "flow-new",
      idempotencyReplay: false
    });
  });

  it("stays put when the deployment run ends without a flow run", async () => {
    const outcome = await followNewFlowRun(
      deploymentRun({ status: "CANCELLED" }),
      undefined,
      async () => null
    );
    expect(outcome).toEqual({ kind: "stuck", status: "CANCELLED" });
  });
});
