import type { Deployment } from "../types";
import { nextCronInstant, normalizeCronExpression, type CronFields } from "./cron";

export const CRON_WILL_NOT_RUN =
  "This cron will not run on this server. The Python scheduler does not fire cron; cron runs only when the Rust scheduler is active.";

export type ScheduleKind = "manual" | "interval" | "cron" | "rrule";
export type IntervalUnit = "seconds" | "minutes" | "hours" | "days";
export type RRuleFreq = "MINUTELY" | "HOURLY" | "DAILY" | "WEEKLY";

export type ScheduleDraft = {
  kind: ScheduleKind;
  intervalValue: string;
  intervalUnit: IntervalUnit;
  cron: string;
  rruleFreq: RRuleFreq;
  rruleInterval: string;
  rruleUntil: string;
};

export type SchedulePreview = {
  summary: string;
  nextRunLabel: string;
  expression: string | null;
  note: string | null;
  warning: string | null;
  error: string | null;
};

export type DeploymentScheduleView = SchedulePreview & {
  kind: ScheduleKind;
  paused: boolean;
};

export type ScheduleUpdate = {
  schedule_enabled: boolean;
  schedule_interval_seconds: number | null;
  schedule_cron: string | null;
  schedule_rrule: string | null;
  schedule_next_run_at?: string | null;
};

const UNIT_SECONDS: Record<IntervalUnit, number> = {
  seconds: 1,
  minutes: 60,
  hours: 3600,
  days: 86400
};

const DOW_LABELS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

export function formatScheduleInstant(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const formatted = new Intl.DateTimeFormat("en-US", {
    timeZone: "UTC",
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hourCycle: "h23"
  }).format(date);
  return `${formatted} UTC`;
}

export function cronTicksActive(deployment: Pick<Deployment, "schedule_cron_ticks">): boolean {
  return deployment.schedule_cron_ticks === true;
}

export function describeDeploymentSchedule(deployment: Deployment, now = new Date()): DeploymentScheduleView {
  const paused = Boolean(deployment.paused);
  if (!deployment.schedule_enabled) {
    return {
      kind: "manual",
      summary: "Manual",
      nextRunLabel: "—",
      expression: null,
      note: "Runs only when you start it.",
      warning: null,
      error: null,
      paused
    };
  }
  if (deployment.schedule_rrule?.trim()) {
    return { ...previewRrule(deployment.schedule_rrule, now), kind: "rrule", paused };
  }
  if (deployment.schedule_cron?.trim()) {
    return {
      ...previewCron(deployment.schedule_cron, cronTicksActive(deployment), deployment.schedule_next_run_at, now),
      kind: "cron",
      paused
    };
  }
  if (deployment.schedule_interval_seconds != null && deployment.schedule_interval_seconds > 0) {
    return {
      ...previewInterval(deployment.schedule_interval_seconds, deployment.schedule_next_run_at),
      kind: "interval",
      paused
    };
  }
  return {
    kind: "manual",
    summary: "Manual",
    nextRunLabel: "—",
    expression: null,
    note: "Runs only when you start it.",
    warning: null,
    error: null,
    paused
  };
}

export function draftFromDeployment(deployment: Deployment): ScheduleDraft {
  const base = emptyDraft();
  if (deployment.schedule_rrule?.trim()) {
    const parsed = parseRrule(deployment.schedule_rrule);
    return {
      ...base,
      kind: deployment.schedule_enabled ? "rrule" : "manual",
      rruleFreq: parsed?.freq ?? "HOURLY",
      rruleInterval: String(parsed?.interval ?? 1),
      rruleUntil: parsed?.until ? isoToDatetimeLocal(parsed.until) : ""
    };
  }
  if (deployment.schedule_cron?.trim()) {
    return { ...base, kind: deployment.schedule_enabled ? "cron" : "manual", cron: deployment.schedule_cron };
  }
  if (deployment.schedule_interval_seconds != null && deployment.schedule_interval_seconds > 0) {
    const interval = intervalParts(deployment.schedule_interval_seconds);
    return {
      ...base,
      kind: deployment.schedule_enabled ? "interval" : "manual",
      intervalValue: interval.value,
      intervalUnit: interval.unit
    };
  }
  return base;
}

export function emptyDraft(): ScheduleDraft {
  return {
    kind: "interval",
    intervalValue: "1",
    intervalUnit: "hours",
    cron: "0 */10 * * * *",
    rruleFreq: "HOURLY",
    rruleInterval: "1",
    rruleUntil: ""
  };
}

export function previewDraft(draft: ScheduleDraft, cronTicks: boolean, now = new Date()): SchedulePreview {
  switch (draft.kind) {
    case "manual":
      return {
        summary: "Manual",
        nextRunLabel: "—",
        expression: null,
        note: "Runs only when you start it.",
        warning: null,
        error: null
      };
    case "interval":
      return previewIntervalDraft(draft);
    case "cron":
      return previewCron(draft.cron, cronTicks, null, now);
    case "rrule":
      try {
        return previewRrule(buildRrule(draft), now);
      } catch (error) {
        return {
          summary: "RRule",
          nextRunLabel: "—",
          expression: null,
          note: null,
          warning: null,
          error: error instanceof Error ? error.message : "Invalid RRule."
        };
      }
    default: {
      const _never: never = draft.kind;
      return _never;
    }
  }
}

export function scheduleUpdate(
  deployment: Deployment,
  draft: ScheduleDraft,
  now = new Date()
): { update: ScheduleUpdate } | { error: string } {
  const preview = previewDraft(draft, cronTicksActive(deployment), now);
  if (preview.error) return { error: preview.error };
  const cleared: ScheduleUpdate = {
    schedule_enabled: false,
    schedule_interval_seconds: null,
    schedule_cron: null,
    schedule_rrule: null,
    schedule_next_run_at: null
  };
  if (draft.kind === "manual") return { update: cleared };

  const same = sameSchedule(deployment, draft);
  if (draft.kind === "interval") {
    const seconds = intervalSeconds(draft);
    return {
      update: {
        schedule_enabled: true,
        schedule_interval_seconds: seconds,
        schedule_cron: null,
        schedule_rrule: null,
        ...(same ? {} : { schedule_next_run_at: null })
      }
    };
  }
  if (draft.kind === "rrule") {
    return {
      update: {
        schedule_enabled: true,
        schedule_interval_seconds: null,
        schedule_cron: null,
        schedule_rrule: buildRrule(draft),
        ...(same ? {} : { schedule_next_run_at: null })
      }
    };
  }
  const normalized = normalizeCronExpression(draft.cron);
  const update: ScheduleUpdate = {
    schedule_enabled: true,
    schedule_interval_seconds: null,
    schedule_cron: normalized.expression,
    schedule_rrule: null
  };
  if (!same) {
    update.schedule_next_run_at = cronTicksActive(deployment)
      ? null
      : nextCronInstant(normalized.expression, now)?.toISOString() ?? null;
  }
  return { update };
}

function previewIntervalDraft(draft: ScheduleDraft): SchedulePreview {
  let seconds = 0;
  try {
    seconds = intervalSeconds(draft);
  } catch (error) {
    return {
      summary: "Interval",
      nextRunLabel: "—",
      expression: null,
      note: null,
      warning: null,
      error: error instanceof Error ? error.message : "Enter a positive interval."
    };
  }
  return previewInterval(seconds, null);
}

function previewInterval(seconds: number, nextRunAt: string | null | undefined): SchedulePreview {
  const next = nextRunAt?.trim()
    ? formatScheduleInstant(nextRunAt)
    : "On the next scheduler check";
  return {
    summary: describeInterval(seconds),
    nextRunLabel: next,
    expression: null,
    note: nextRunAt?.trim() ? null : "The first run is due as soon as the scheduler checks, then on this interval.",
    warning: null,
    error: null
  };
}

function previewCron(
  input: string,
  cronTicks: boolean,
  storedNext: string | null | undefined,
  now: Date
): SchedulePreview {
  try {
    const normalized = normalizeCronExpression(input);
    const summary = describeCron(normalized.expression, normalized.fields);
    const upcoming = nextCronInstant(normalized.expression, now);
    if (!cronTicks) {
      return {
        summary,
        nextRunLabel: "Will not run",
        expression: normalized.expression,
        note: normalized.note,
        warning: CRON_WILL_NOT_RUN,
        error: upcoming ? null : "This cron has no upcoming occurrence."
      };
    }
    const nextLabel = storedNext?.trim()
      ? formatScheduleInstant(storedNext)
      : upcoming
        ? formatScheduleInstant(upcoming.toISOString())
        : "—";
    return {
      summary,
      nextRunLabel: nextLabel,
      expression: normalized.expression,
      note: normalized.note,
      warning: null,
      error: upcoming || storedNext?.trim() ? null : "This cron has no upcoming occurrence."
    };
  } catch (error) {
    return {
      summary: "Cron",
      nextRunLabel: "—",
      expression: null,
      note: null,
      warning: cronTicks ? null : CRON_WILL_NOT_RUN,
      error: error instanceof Error ? error.message : "Invalid cron expression."
    };
  }
}

function previewRrule(expression: string, now: Date): SchedulePreview {
  try {
    const parsed = parseRrule(expression);
    if (!parsed) throw new Error("Enter a frequency, a positive interval, and an optional UNTIL.");
    const stepMs = rruleStepMs(parsed.freq, parsed.interval);
    const next = new Date(now.getTime() + stepMs);
    if (parsed.until && next.getTime() > parsed.until.getTime()) {
      throw new Error("RRule has no upcoming occurrence before UNTIL.");
    }
    const untilNote = parsed.until ? ` until ${formatScheduleInstant(parsed.until.toISOString())}` : "";
    return {
      summary: `${describeRrule(parsed.freq, parsed.interval)}${untilNote}`,
      nextRunLabel: formatScheduleInstant(next.toISOString()),
      expression,
      note: "RRule supports FREQ of MINUTELY, HOURLY, DAILY, or WEEKLY, a positive INTERVAL, and an optional UNTIL.",
      warning: null,
      error: null
    };
  } catch (error) {
    return {
      summary: "RRule",
      nextRunLabel: "—",
      expression,
      note: null,
      warning: null,
      error: error instanceof Error ? error.message : "Invalid RRule."
    };
  }
}

function describeRrule(freq: RRuleFreq, interval: number): string {
  const unit = { MINUTELY: "minute", HOURLY: "hour", DAILY: "day", WEEKLY: "week" }[freq];
  return countPhrase(interval, unit);
}

function describeInterval(seconds: number): string {
  if (seconds % 86400 === 0) return countPhrase(seconds / 86400, "day");
  if (seconds % 3600 === 0) return countPhrase(seconds / 3600, "hour");
  if (seconds % 60 === 0) return countPhrase(seconds / 60, "minute");
  return countPhrase(seconds, "second");
}

function countPhrase(count: number, unit: string): string {
  if (count === 1) return `Every ${unit}`;
  return `Every ${count} ${unit}s`;
}

function describeCron(expression: string, fields: CronFields): string {
  const shorthand = expression.trim().toLowerCase();
  if (shorthand === "@hourly") return "Every hour";
  if (shorthand === "@daily") return "Every day at 00:00 UTC";
  if (shorthand === "@weekly") return "Every Sunday at 00:00 UTC";
  if (shorthand === "@monthly") return "Every month on the 1st at 00:00 UTC";
  if (shorthand === "@yearly") return "Every year on January 1 at 00:00 UTC";
  if (!isFull(fields.dom, 1, 31) || !isFull(fields.month, 1, 12) || !isFull(fields.year, 1970, 2100)) {
    return `Cron ${expression}`;
  }
  if (isFull(fields.dow, 1, 7) && isSingleton(fields.second, 0) && isStep(fields.minute, 0, 59) && isFull(fields.hour, 0, 23)) {
    const step = fields.minute.values[1] - fields.minute.values[0];
    return step === 1 ? "Every minute" : `Every ${step} minutes`;
  }
  if (isFull(fields.dow, 1, 7) && isSingleton(fields.second, 0) && isFull(fields.minute, 0, 59) && isFull(fields.hour, 0, 23)) {
    return "Every minute";
  }
  if (isFull(fields.dow, 1, 7) && isSingleton(fields.second, 0) && isSingleton(fields.minute, 0) && isStep(fields.hour, 0, 23)) {
    const step = fields.hour.values.length === 1 ? 24 : fields.hour.values[1] - fields.hour.values[0];
    return step === 1 ? "Every hour" : `Every ${step} hours`;
  }
  if (isSingleton(fields.second, 0) && fields.minute.values.length === 1 && fields.hour.values.length === 1) {
    const clock = clockLabel(fields.hour.values[0], fields.minute.values[0]);
    if (isFull(fields.dow, 1, 7)) return `Every day at ${clock} UTC`;
    return `Every ${dowPhrase(fields.dow.values)} at ${clock} UTC`;
  }
  if (isStep(fields.second, 0, 59) && isFull(fields.minute, 0, 59) && isFull(fields.hour, 0, 23) && isFull(fields.dow, 1, 7)) {
    const step = fields.second.values[1] - fields.second.values[0];
    return step === 1 ? "Every second" : `Every ${step} seconds`;
  }
  return `Cron ${expression}`;
}

function dowPhrase(values: number[]): string {
  const labels = values.map((value) => DOW_LABELS[value - 1] ?? String(value));
  if (labels.length === 1) return labels[0];
  const contiguous = values.every((value, index) => index === 0 || value === values[index - 1] + 1);
  if (contiguous && sameSet(values, [2, 3, 4, 5, 6])) return "weekday";
  if (contiguous) return `${labels[0]} through ${labels[labels.length - 1]}`;
  return labels.join(", ");
}

function clockLabel(hour: number, minute: number): string {
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

function isFull(field: CronFields["hour"], min: number, max: number): boolean {
  return field.values.length === max - min + 1 && field.values[0] === min && field.values[field.values.length - 1] === max;
}

function isSingleton(field: CronFields["second"], value: number): boolean {
  return field.values.length === 1 && field.values[0] === value;
}

function isStep(field: CronFields["minute"], min: number, max: number): boolean {
  if (field.values.length < 2 || field.values[0] !== min) return false;
  const step = field.values[1] - field.values[0];
  if (step <= 0) return false;
  const expected: number[] = [];
  for (let value = min; value <= max; value += step) expected.push(value);
  return sameSet(field.values, expected);
}

function sameSet(left: number[], right: number[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function intervalSeconds(draft: ScheduleDraft): number {
  const value = Number(draft.intervalValue);
  if (!Number.isInteger(value) || value <= 0) throw new Error("Interval must be a positive whole number.");
  const seconds = value * UNIT_SECONDS[draft.intervalUnit];
  if (seconds > 366 * 86400) throw new Error("Interval must be under a year.");
  return seconds;
}

function intervalParts(seconds: number): { value: string; unit: IntervalUnit } {
  if (seconds % 86400 === 0) return { value: String(seconds / 86400), unit: "days" };
  if (seconds % 3600 === 0) return { value: String(seconds / 3600), unit: "hours" };
  if (seconds % 60 === 0) return { value: String(seconds / 60), unit: "minutes" };
  return { value: String(seconds), unit: "seconds" };
}

type ParsedRRule = { freq: RRuleFreq; interval: number; until: Date | null };

function parseRrule(expression: string): ParsedRRule | null {
  const parts = new Map<string, string>();
  for (const raw of expression.split(";")) {
    const piece = raw.trim();
    if (!piece) continue;
    const eq = piece.indexOf("=");
    if (eq <= 0) throw new Error(`Invalid RRule component: ${piece}`);
    const key = piece.slice(0, eq).trim().toUpperCase();
    if (!["FREQ", "INTERVAL", "UNTIL"].includes(key)) {
      throw new Error(
        key === "COUNT"
          ? "RRule COUNT is not supported. Use UNTIL, or start a fixed number of runs yourself."
          : `Unsupported RRule component: ${key}`
      );
    }
    parts.set(key, piece.slice(eq + 1).trim());
  }
  const freq = parts.get("FREQ")?.toUpperCase();
  if (freq !== "MINUTELY" && freq !== "HOURLY" && freq !== "DAILY" && freq !== "WEEKLY") {
    throw new Error("RRule FREQ must be MINUTELY, HOURLY, DAILY, or WEEKLY.");
  }
  const interval = Number(parts.get("INTERVAL") ?? "1");
  if (!Number.isInteger(interval) || interval <= 0) throw new Error("RRule INTERVAL must be a positive whole number.");
  const untilRaw = parts.get("UNTIL");
  return { freq, interval, until: untilRaw ? parseUntil(untilRaw) : null };
}

function parseUntil(value: string): Date {
  const iso = value.includes("-") || value.endsWith("Z") ? value : value.replace(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z?$/, "$1-$2-$3T$4:$5:$6Z");
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) throw new Error("RRule UNTIL is not a time.");
  return date;
}

function buildRrule(draft: ScheduleDraft): string {
  const interval = Number(draft.rruleInterval);
  if (!Number.isInteger(interval) || interval <= 0) throw new Error("RRule INTERVAL must be a positive whole number.");
  const parts = [`FREQ=${draft.rruleFreq}`, `INTERVAL=${interval}`];
  if (draft.rruleUntil.trim()) {
    const until = new Date(draft.rruleUntil);
    if (Number.isNaN(until.getTime())) throw new Error("UNTIL is not a time.");
    parts.push(`UNTIL=${until.toISOString().replace(/\.\d{3}Z$/, "Z")}`);
  }
  return parts.join(";");
}

function rruleStepMs(freq: RRuleFreq, interval: number): number {
  const unit = { MINUTELY: 60_000, HOURLY: 3_600_000, DAILY: 86_400_000, WEEKLY: 604_800_000 }[freq];
  return unit * interval;
}

function isoToDatetimeLocal(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function sameSchedule(deployment: Deployment, draft: ScheduleDraft): boolean {
  if (!deployment.schedule_enabled || draft.kind === "manual") return false;
  if (draft.kind === "interval") {
    return deployment.schedule_interval_seconds === intervalSeconds(draft) && !deployment.schedule_cron && !deployment.schedule_rrule;
  }
  if (draft.kind === "rrule") {
    return deployment.schedule_rrule === buildRrule(draft);
  }
  try {
    return deployment.schedule_cron === normalizeCronExpression(draft.cron).expression;
  } catch {
    return false;
  }
}
