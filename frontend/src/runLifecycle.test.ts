import { describe, expect, it } from "vitest";
import type { FlowRun } from "./types";
import {
  canPauseRun,
  canResumeRun,
  canRetryRun,
  formatRunDuration,
  isGatePaused,
  isOperatorPause,
  RETRY_UNAVAILABLE_NOTE,
  showRetryUnavailableNote,
  taskOutcomeLabel
} from "./runLifecycle";

function run(partial: Partial<FlowRun>): FlowRun {
  return {
    id: "r1",
    name: "demo",
    state: "RUNNING",
    version: 1,
    created_at: "2026-08-15T00:00:00+00:00",
    updated_at: "2026-08-15T00:00:02+00:00",
    ...partial
  };
}

describe("runLifecycle", () => {
  it("requires an explicit pause mode and hides resume for gate waits", () => {
    const active = run({ state: "RUNNING" });
    expect(canPauseRun(active)).toBe(true);
    expect(canResumeRun(active)).toBe(false);

    const draining = run({ state: "RUNNING", lifecycle_action: "pause", pause_drain_pending: true });
    expect(canPauseRun(draining)).toBe(false);
    expect(canResumeRun(draining)).toBe(false);

    const operatorPaused = run({
      state: "PAUSED",
      lifecycle_action: "pause",
      interrupt_mode: "drain"
    });
    expect(isOperatorPause(operatorPaused)).toBe(true);
    expect(canResumeRun(operatorPaused)).toBe(true);

    const gatePaused = run({ state: "PAUSED" });
    expect(isGatePaused(gatePaused)).toBe(true);
    expect(canResumeRun(gatePaused)).toBe(false);
  });

  it("labels skipped cache hits vs recomputed resume tasks", () => {
    expect(taskOutcomeLabel({ cacheHit: true, isResumeAttempt: true, state: "COMPLETED" })).toBe(
      "skipped"
    );
    expect(taskOutcomeLabel({ cacheHit: false, isResumeAttempt: true, state: "COMPLETED" })).toBe(
      "recomputed"
    );
    expect(taskOutcomeLabel({ cacheHit: false, isResumeAttempt: false, state: "COMPLETED" })).toBe(
      null
    );
  });

  it("shows Retry only for deployment-created terminal runs", () => {
    for (const state of ["FAILED", "CANCELLED", "COMPLETED"] as const) {
      expect(canRetryRun(run({ state, deployment_id: "dep-1" }))).toBe(true);
      expect(showRetryUnavailableNote(run({ state, deployment_id: "dep-1" }))).toBe(false);
      expect(canRetryRun(run({ state }))).toBe(false);
      expect(showRetryUnavailableNote(run({ state }))).toBe(true);
    }
    expect(RETRY_UNAVAILABLE_NOTE).toMatch(/not started from a deployment/);
    const running = run({ state: "RUNNING", deployment_id: "dep-1" });
    expect(canRetryRun(running)).toBe(false);
    expect(showRetryUnavailableNote(running)).toBe(false);
    expect(canPauseRun(running)).toBe(true);
  });

  it("formats created-to-updated duration", () => {
    expect(formatRunDuration("2026-08-15T00:00:00Z", "2026-08-15T00:00:02.500Z")).toBe("2.5s");
  });
});
