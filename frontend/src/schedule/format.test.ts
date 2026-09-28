import { describe, expect, it } from "vitest";
import type { Deployment } from "../types";
import {
  CRON_WILL_NOT_RUN,
  describeDeploymentSchedule,
  previewDraft,
  scheduleUpdate,
  type ScheduleDraft
} from "./format";

const now = new Date("2026-09-27T12:00:30Z");

function deployment(overrides: Partial<Deployment> = {}): Deployment {
  return {
    id: "dep-1",
    name: "demo",
    flow_name: "simple_flow",
    default_parameters: {},
    paused: false,
    schedule_enabled: false,
    schedule_cron_ticks: false,
    created_at: "2026-09-27T00:00:00Z",
    updated_at: "2026-09-27T00:00:00Z",
    ...overrides
  };
}

describe("deployment schedule words", () => {
  it("reads an empty deployment as manual", () => {
    const view = describeDeploymentSchedule(deployment(), now);
    expect(view.kind).toBe("manual");
    expect(view.summary).toBe("Manual");
    expect(view.nextRunLabel).toBe("—");
    expect(view.warning).toBeNull();
  });

  it("describes an interval and its next run", () => {
    const view = describeDeploymentSchedule(
      deployment({
        schedule_enabled: true,
        schedule_interval_seconds: 3600,
        schedule_next_run_at: "2026-09-27T13:00:00Z"
      }),
      now
    );
    expect(view.summary).toBe("Every hour");
    expect(view.nextRunLabel).toContain("13:00");
    expect(view.nextRunLabel).toContain("UTC");
  });

  it("does not present a cron as running when the Python scheduler will not fire it", () => {
    const view = describeDeploymentSchedule(
      deployment({
        schedule_enabled: true,
        schedule_cron: "0 */10 * * * *",
        schedule_next_run_at: "2026-09-27T12:10:00Z",
        schedule_cron_ticks: false
      }),
      now
    );
    expect(view.summary).toBe("Every 10 minutes");
    expect(view.nextRunLabel).toBe("Will not run");
    expect(view.warning).toBe(CRON_WILL_NOT_RUN);
  });

  it("shows the next cron run when the Rust scheduler is active", () => {
    const view = describeDeploymentSchedule(
      deployment({
        schedule_enabled: true,
        schedule_cron: "0 0 9 * * *",
        schedule_next_run_at: "2026-09-28T09:00:00Z",
        schedule_cron_ticks: true
      }),
      now
    );
    expect(view.summary).toBe("Every day at 09:00 UTC");
    expect(view.nextRunLabel).toContain("09:00");
    expect(view.warning).toBeNull();
  });

  it("describes the supported RRule subset and rejects COUNT", () => {
    const view = describeDeploymentSchedule(
      deployment({
        schedule_enabled: true,
        schedule_rrule: "FREQ=HOURLY;INTERVAL=2"
      }),
      now
    );
    expect(view.summary).toBe("Every 2 hours");
    expect(view.error).toBeNull();
    const counted = describeDeploymentSchedule(
      deployment({ schedule_enabled: true, schedule_rrule: "FREQ=DAILY;COUNT=3" }),
      now
    );
    expect(counted.error).toMatch(/COUNT/);
    const daily = previewDraft(
      {
        kind: "rrule",
        intervalValue: "1",
        intervalUnit: "hours",
        cron: "",
        rruleFreq: "DAILY",
        rruleInterval: "1",
        rruleUntil: ""
      },
      false,
      now
    );
    expect(daily.summary).toBe("Every day");
    const draft: ScheduleDraft = {
      kind: "rrule",
      intervalValue: "1",
      intervalUnit: "hours",
      cron: "",
      rruleFreq: "DAILY",
      rruleInterval: "1",
      rruleUntil: ""
    };
    expect(previewDraft({ ...draft, rruleInterval: "0" }, false, now).error).toMatch(/INTERVAL/);
  });

  it("stores a cron without claiming a tick when Rust is inactive", () => {
    const draft: ScheduleDraft = {
      kind: "cron",
      intervalValue: "1",
      intervalUnit: "hours",
      cron: "*/10 * * * *",
      rruleFreq: "HOURLY",
      rruleInterval: "1",
      rruleUntil: ""
    };
    const preview = previewDraft(draft, false, now);
    expect(preview.summary).toBe("Every 10 minutes");
    expect(preview.expression).toBe("0 */10 * * * *");
    expect(preview.nextRunLabel).toBe("Will not run");
    const update = scheduleUpdate(deployment({ schedule_cron_ticks: false }), draft, now);
    expect(update).toEqual({
      update: {
        schedule_enabled: true,
        schedule_interval_seconds: null,
        schedule_cron: "0 */10 * * * *",
        schedule_rrule: null,
        schedule_next_run_at: "2026-09-27T12:10:00.000Z"
      }
    });
  });

  it("asks Rust to compute the next cron time when ticks are active", () => {
    const draft: ScheduleDraft = {
      kind: "cron",
      intervalValue: "1",
      intervalUnit: "hours",
      cron: "0 */10 * * * *",
      rruleFreq: "HOURLY",
      rruleInterval: "1",
      rruleUntil: ""
    };
    const update = scheduleUpdate(deployment({ schedule_cron_ticks: true }), draft, now);
    expect(update).toEqual({
      update: {
        schedule_enabled: true,
        schedule_interval_seconds: null,
        schedule_cron: "0 */10 * * * *",
        schedule_rrule: null,
        schedule_next_run_at: null
      }
    });
  });
});