import { api } from "./api";
import type { DeploymentRun } from "./types";

export type FollowNewFlowRunResult =
  | { kind: "open"; flowRunId: string; idempotencyReplay: boolean }
  | { kind: "stuck"; status: string };

const TERMINAL_WITHOUT_FLOW = new Set(["CANCELLED", "FAILED"]);

export function idempotencyReplayNote(state: unknown): string | null {
  if (!state || typeof state !== "object" || !("idempotencyReplay" in state)) {
    return null;
  }
  return (state as { idempotencyReplay?: boolean }).idempotencyReplay
    ? "This idempotency key was already used."
    : null;
}

export async function followNewFlowRun(
  created: DeploymentRun,
  idempotencyKey: string | undefined,
  load: (deploymentRunId: string) => Promise<DeploymentRun | null> = defaultLoad,
  timeoutMs = 20_000
): Promise<FollowNewFlowRunResult> {
  const replay = Boolean(idempotencyKey);
  if (created.flow_run_id) {
    return { kind: "open", flowRunId: created.flow_run_id, idempotencyReplay: replay };
  }
  if (TERMINAL_WITHOUT_FLOW.has(created.status)) {
    return { kind: "stuck", status: created.status };
  }
  const deadline = Date.now() + timeoutMs;
  let latest = created;
  while (Date.now() < deadline) {
    await delay(250);
    const next = await load(created.id);
    if (!next) continue;
    latest = next;
    if (next.flow_run_id) {
      return { kind: "open", flowRunId: next.flow_run_id, idempotencyReplay: false };
    }
    if (TERMINAL_WITHOUT_FLOW.has(next.status)) {
      return { kind: "stuck", status: next.status };
    }
  }
  return { kind: "stuck", status: latest.status || "SCHEDULED" };
}

async function defaultLoad(deploymentRunId: string): Promise<DeploymentRun | null> {
  const page = await api.listDeploymentRuns();
  return page.items.find((item) => item.id === deploymentRunId) ?? null;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}
