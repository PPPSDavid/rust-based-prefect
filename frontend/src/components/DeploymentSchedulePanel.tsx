import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import {
  cronTicksActive,
  describeDeploymentSchedule,
  draftFromDeployment,
  emptyDraft,
  previewDraft,
  scheduleUpdate,
  type ScheduleDraft,
  type ScheduleKind
} from "../schedule/format";
import type { Deployment } from "../types";
import { ActionButton } from "./ActionButton";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { Input } from "./ui/input";

function errorDetail(error: unknown): string {
  if (!(error instanceof Error) || !error.message) return "Could not update the schedule.";
  try {
    const parsed = JSON.parse(error.message) as { detail?: unknown };
    if (typeof parsed.detail === "string" && parsed.detail.trim()) return parsed.detail;
  } catch {
    return error.message;
  }
  return error.message;
}

function draftForEdit(deployment: Deployment): ScheduleDraft {
  const stored = draftFromDeployment(deployment);
  if (deployment.schedule_enabled) return stored;
  if (
    deployment.schedule_cron?.trim() ||
    deployment.schedule_rrule?.trim() ||
    (deployment.schedule_interval_seconds ?? 0) > 0
  ) {
    const kind: ScheduleKind = deployment.schedule_rrule?.trim()
      ? "rrule"
      : deployment.schedule_cron?.trim()
        ? "cron"
        : "interval";
    return { ...stored, kind };
  }
  return emptyDraft();
}

export function DeploymentSchedulePanel({ deployment }: { deployment: Deployment }) {
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [editing, setEditing] = useState(params.get("schedule") === "edit");
  const [draft, setDraft] = useState<ScheduleDraft>(() =>
    params.get("schedule") === "edit" ? draftForEdit(deployment) : emptyDraft()
  );
  const view = describeDeploymentSchedule(deployment);
  const preview = previewDraft(draft, cronTicksActive(deployment));

  const save = useMutation({
    mutationFn: (update: Parameters<typeof api.patchDeployment>[1]) => api.patchDeployment(deployment.id, update),
    onSuccess: (updated) => {
      queryClient.setQueryData(["deployment", deployment.id], updated);
      void queryClient.invalidateQueries({ queryKey: ["deployments"] });
      void queryClient.invalidateQueries({ queryKey: ["deployment", deployment.id] });
      setEditing(false);
      if (params.get("schedule")) {
        const next = new URLSearchParams(params);
        next.delete("schedule");
        setParams(next, { replace: true });
      }
    }
  });

  function beginEdit() {
    setDraft(draftForEdit(deployment));
    setEditing(true);
    save.reset();
  }

  function onSave() {
    const result = scheduleUpdate(deployment, draft);
    if ("error" in result) return;
    save.mutate(result.update);
  }

  return (
    <Card className="schedule-panel">
      <CardHeader>
        <CardTitle>Schedule</CardTitle>
      </CardHeader>
      <CardContent>
        {editing ? (
          <ScheduleEditor
            draft={draft}
            previewSummary={preview.summary}
            previewNext={preview.nextRunLabel}
            previewExpression={preview.expression}
            previewNote={preview.note}
            previewWarning={preview.warning}
            previewError={preview.error}
            saveError={save.isError ? errorDetail(save.error) : null}
            saving={save.isPending}
            onChange={setDraft}
            onCancel={() => setEditing(false)}
            onSave={onSave}
          />
        ) : (
          <ScheduleReadout
            summary={view.summary}
            nextRunLabel={view.nextRunLabel}
            expression={view.expression}
            note={view.note}
            warning={view.warning}
            paused={view.paused && view.kind !== "manual"}
            actionLabel={view.kind === "manual" ? "Add schedule" : "Edit schedule"}
            onEdit={beginEdit}
          />
        )}
      </CardContent>
    </Card>
  );
}

function ScheduleReadout({
  summary,
  nextRunLabel,
  expression,
  note,
  warning,
  paused,
  actionLabel,
  onEdit
}: {
  summary: string;
  nextRunLabel: string;
  expression: string | null;
  note: string | null;
  warning: string | null;
  paused: boolean;
  actionLabel: string;
  onEdit: () => void;
}) {
  return (
    <div>
      <p>{summary}</p>
      {expression ? <p className="schedule-expression">{expression}</p> : null}
      <p>Next run: {nextRunLabel}</p>
      {note ? <p className="schedule-note">{note}</p> : null}
      {warning ? <p className="schedule-warning">{warning}</p> : null}
      {paused ? <p className="schedule-warning">Paused. This schedule waits until you resume the deployment.</p> : null}
      <ActionButton onClick={onEdit}>{actionLabel}</ActionButton>
    </div>
  );
}

function ScheduleEditor({
  draft,
  previewSummary,
  previewNext,
  previewExpression,
  previewNote,
  previewWarning,
  previewError,
  saveError,
  saving,
  onChange,
  onCancel,
  onSave
}: {
  draft: ScheduleDraft;
  previewSummary: string;
  previewNext: string;
  previewExpression: string | null;
  previewNote: string | null;
  previewWarning: string | null;
  previewError: string | null;
  saveError: string | null;
  saving: boolean;
  onChange: (draft: ScheduleDraft) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  return (
    <div className="schedule-form">
      <label className="field-label">
        Schedule kind
        <select
          className="field-input"
          aria-label="Schedule kind"
          value={draft.kind}
          onChange={(event) => onChange({ ...draft, kind: event.target.value as ScheduleKind })}
        >
          <option value="manual">Manual</option>
          <option value="interval">Interval</option>
          <option value="cron">Cron</option>
          <option value="rrule">RRule</option>
        </select>
      </label>
      {draft.kind === "interval" ? <IntervalFields draft={draft} onChange={onChange} /> : null}
      {draft.kind === "cron" ? <CronFields draft={draft} onChange={onChange} /> : null}
      {draft.kind === "rrule" ? <RRuleFields draft={draft} onChange={onChange} /> : null}
      <div className="schedule-preview" aria-live="polite">
        <p>{previewSummary}</p>
        {previewExpression ? <p className="schedule-expression">{previewExpression}</p> : null}
        <p>Next run: {previewNext}</p>
        {previewNote ? <p className="schedule-note">{previewNote}</p> : null}
        {previewWarning ? <p className="schedule-warning">{previewWarning}</p> : null}
        {previewError ? <p className="form-error">{previewError}</p> : null}
      </div>
      {saveError ? <p className="form-error">{saveError}</p> : null}
      <div className="modal-actions">
        <ActionButton onClick={onCancel}>Cancel</ActionButton>
        <ActionButton variant="primary" disabled={Boolean(previewError) || saving} onClick={onSave}>
          Save schedule
        </ActionButton>
      </div>
    </div>
  );
}

function IntervalFields({ draft, onChange }: { draft: ScheduleDraft; onChange: (draft: ScheduleDraft) => void }) {
  return (
    <div className="toolbar">
      <label className="field-label">
        Every
        <Input
          aria-label="Interval amount"
          type="number"
          min={1}
          value={draft.intervalValue}
          onChange={(event) => onChange({ ...draft, intervalValue: event.target.value })}
        />
      </label>
      <label className="field-label">
        Unit
        <select
          className="field-input"
          aria-label="Interval unit"
          value={draft.intervalUnit}
          onChange={(event) =>
            onChange({ ...draft, intervalUnit: event.target.value as ScheduleDraft["intervalUnit"] })
          }
        >
          <option value="seconds">seconds</option>
          <option value="minutes">minutes</option>
          <option value="hours">hours</option>
          <option value="days">days</option>
        </select>
      </label>
    </div>
  );
}

function CronFields({ draft, onChange }: { draft: ScheduleDraft; onChange: (draft: ScheduleDraft) => void }) {
  return (
    <label className="field-label">
      Cron expression
      <Input
        aria-label="Cron expression"
        value={draft.cron}
        spellCheck={false}
        onChange={(event) => onChange({ ...draft, cron: event.target.value })}
      />
      <p className="schedule-note">
        Seconds, minutes, hours, day of month, month, day of week. Sunday is 1. Example: 0 */10 * * * * or @hourly.
      </p>
    </label>
  );
}

function RRuleFields({ draft, onChange }: { draft: ScheduleDraft; onChange: (draft: ScheduleDraft) => void }) {
  return (
    <>
      <label className="field-label">
        Frequency
        <select
          className="field-input"
          aria-label="RRule frequency"
          value={draft.rruleFreq}
          onChange={(event) => onChange({ ...draft, rruleFreq: event.target.value as ScheduleDraft["rruleFreq"] })}
        >
          <option value="MINUTELY">Minutely</option>
          <option value="HOURLY">Hourly</option>
          <option value="DAILY">Daily</option>
          <option value="WEEKLY">Weekly</option>
        </select>
      </label>
      <label className="field-label">
        Interval
        <Input
          aria-label="RRule interval"
          type="number"
          min={1}
          value={draft.rruleInterval}
          onChange={(event) => onChange({ ...draft, rruleInterval: event.target.value })}
        />
      </label>
      <label className="field-label">
        Until (optional)
        <Input
          aria-label="RRule until"
          type="datetime-local"
          value={draft.rruleUntil}
          onChange={(event) => onChange({ ...draft, rruleUntil: event.target.value })}
        />
      </label>
    </>
  );
}
