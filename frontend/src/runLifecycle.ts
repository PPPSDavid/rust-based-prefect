import type { FlowRun } from "./types";

const PAUSEABLE = new Set(["SCHEDULED", "PENDING", "RUNNING"]);

export function isOperatorPause(run: FlowRun): boolean {
  return run.lifecycle_action === "pause";
}

export function isGatePaused(run: FlowRun): boolean {
  return run.state === "PAUSED" && run.lifecycle_action !== "pause";
}

export function canPauseRun(run: FlowRun): boolean {
  return PAUSEABLE.has(run.state) && !run.pause_drain_pending;
}

export function canResumeRun(run: FlowRun): boolean {
  if (!isOperatorPause(run) || run.pause_drain_pending) {
    return false;
  }
  return run.state === "PAUSED" || run.state === "RUNNING";
}

export function taskOutcomeLabel(opts: {
  cacheHit: boolean;
  isResumeAttempt: boolean;
  state: string;
}): "skipped" | "recomputed" | null {
  if (opts.cacheHit) {
    return "skipped";
  }
  if (opts.isResumeAttempt && opts.state === "COMPLETED") {
    return "recomputed";
  }
  return null;
}

export function formatRunDuration(createdAt: string, updatedAt: string): string {
  const ms = new Date(updatedAt).getTime() - new Date(createdAt).getTime();
  if (!Number.isFinite(ms) || ms < 0) {
    return "—";
  }
  if (ms < 1000) {
    return `${Math.round(ms)}ms`;
  }
  const seconds = ms / 1000;
  if (seconds < 60) {
    return `${seconds.toFixed(1)}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const rem = Math.round(seconds % 60);
  return `${minutes}m ${rem}s`;
}
