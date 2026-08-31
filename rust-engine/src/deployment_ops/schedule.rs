use std::str::FromStr;

use chrono::{DateTime, Duration, Utc};
use cron::Schedule;
use serde_json::Value;

pub(crate) fn next_cron_occurrence(
    expr: &str,
    after: DateTime<Utc>,
) -> Result<DateTime<Utc>, String> {
    let schedule = Schedule::from_str(expr.trim()).map_err(|e| e.to_string())?;
    schedule
        .after(&after)
        .next()
        .ok_or_else(|| "cron expression has no upcoming occurrence".to_string())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RRuleFrequency {
    Minutely,
    Hourly,
    Daily,
    Weekly,
}

#[derive(Debug, Clone)]
struct SimpleRRule {
    freq: RRuleFrequency,
    interval: i64,
    until: Option<DateTime<Utc>>,
}

fn parse_rrule_datetime(value: &str) -> Result<DateTime<Utc>, String> {
    if let Ok(dt) = DateTime::parse_from_rfc3339(value) {
        return Ok(dt.with_timezone(&Utc));
    }
    let normalized = value.trim().trim_end_matches('Z');
    chrono::NaiveDateTime::parse_from_str(normalized, "%Y%m%dT%H%M%S")
        .map(|dt| dt.and_utc())
        .map_err(|e| format!("invalid RRule UNTIL: {e}"))
}

fn parse_simple_rrule(expr: &str) -> Result<SimpleRRule, String> {
    let mut freq: Option<RRuleFrequency> = None;
    let mut interval: i64 = 1;
    let mut until: Option<DateTime<Utc>> = None;
    for part in expr.split(';') {
        let trimmed = part.trim();
        if trimmed.is_empty() {
            continue;
        }
        let Some((raw_key, raw_value)) = trimmed.split_once('=') else {
            return Err(format!("invalid RRule component: {trimmed}"));
        };
        let key = raw_key.trim().to_ascii_uppercase();
        let value = raw_value.trim();
        match key.as_str() {
            "FREQ" => {
                freq = Some(match value.to_ascii_uppercase().as_str() {
                    "MINUTELY" => RRuleFrequency::Minutely,
                    "HOURLY" => RRuleFrequency::Hourly,
                    "DAILY" => RRuleFrequency::Daily,
                    "WEEKLY" => RRuleFrequency::Weekly,
                    other => return Err(format!("unsupported RRule FREQ: {other}")),
                });
            }
            "INTERVAL" => {
                interval = value
                    .parse::<i64>()
                    .map_err(|e| format!("invalid RRule INTERVAL: {e}"))?;
                if interval <= 0 {
                    return Err("RRule INTERVAL must be positive".to_string());
                }
            }
            "UNTIL" => {
                until = Some(parse_rrule_datetime(value)?);
            }
            "COUNT" => {
                return Err(
                    "RRule COUNT is not supported; use UNTIL or trigger fixed runs manually"
                        .to_string(),
                );
            }
            _ => {
                return Err(format!("unsupported RRule component: {key}"));
            }
        }
    }
    Ok(SimpleRRule {
        freq: freq.ok_or_else(|| "RRule FREQ is required".to_string())?,
        interval,
        until,
    })
}

pub(crate) fn next_rrule_occurrence(
    expr: &str,
    after: DateTime<Utc>,
) -> Result<DateTime<Utc>, String> {
    let rule = parse_simple_rrule(expr)?;
    let step = match rule.freq {
        RRuleFrequency::Minutely => Duration::minutes(rule.interval),
        RRuleFrequency::Hourly => Duration::hours(rule.interval),
        RRuleFrequency::Daily => Duration::days(rule.interval),
        RRuleFrequency::Weekly => Duration::weeks(rule.interval),
    };
    let next = after + step;
    if let Some(until) = rule.until {
        if next > until {
            return Err("RRule has no upcoming occurrence before UNTIL".to_string());
        }
    }
    Ok(next)
}

pub(crate) struct ScheduleFields {
    pub interval: Option<i64>,
    pub cron: Option<String>,
    pub rrule: Option<String>,
    pub next_run_at: Option<String>,
    pub enabled: bool,
}

impl ScheduleFields {
    pub(crate) fn from_create_body(body: &Value) -> Self {
        Self {
            interval: body
                .get("schedule_interval_seconds")
                .and_then(|v| v.as_i64()),
            cron: body
                .get("schedule_cron")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            rrule: body
                .get("schedule_rrule")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            next_run_at: body
                .get("schedule_next_run_at")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            enabled: body
                .get("schedule_enabled")
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
        }
    }

    pub(crate) fn from_row(current: &Value) -> Self {
        Self {
            interval: current
                .get("schedule_interval_seconds")
                .and_then(|v| v.as_i64()),
            cron: current
                .get("schedule_cron")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            rrule: current
                .get("schedule_rrule")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            next_run_at: current
                .get("schedule_next_run_at")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            enabled: current
                .get("schedule_enabled")
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
        }
    }

    pub(crate) fn apply_body(&mut self, body: &Value) {
        if let Some(v) = body.get("schedule_interval_seconds") {
            self.interval = if v.is_null() { None } else { v.as_i64() };
        }
        if let Some(v) = body.get("schedule_cron") {
            self.cron = if v.is_null() {
                None
            } else {
                v.as_str().map(|s| s.to_string())
            };
        }
        if let Some(v) = body.get("schedule_rrule") {
            self.rrule = if v.is_null() {
                None
            } else {
                v.as_str().map(|s| s.to_string())
            };
        }
        if let Some(v) = body.get("schedule_next_run_at") {
            self.next_run_at = if v.is_null() {
                None
            } else {
                v.as_str().map(|s| s.to_string())
            };
        }
        if let Some(v) = body.get("schedule_enabled") {
            if !v.is_null() {
                self.enabled = v.as_bool().unwrap_or(false);
            }
        }
    }

    pub(crate) fn normalize_exclusive(&mut self) {
        if self
            .rrule
            .as_ref()
            .map(|s| !s.trim().is_empty())
            .unwrap_or(false)
        {
            self.interval = None;
            self.cron = None;
        } else if self
            .cron
            .as_ref()
            .map(|s| !s.trim().is_empty())
            .unwrap_or(false)
        {
            self.rrule = None;
            self.interval = None;
        } else if self.interval.map(|s| s > 0).unwrap_or(false) {
            self.cron = None;
            self.rrule = None;
        }
    }

    pub(crate) fn fill_next_run(&mut self) -> Result<(), String> {
        if !self.enabled {
            return Ok(());
        }
        if self.interval.map(|s| s > 0).unwrap_or(false) && self.next_run_at.is_none() {
            self.next_run_at = Some(Utc::now().to_rfc3339());
        }
        if self
            .cron
            .as_ref()
            .map(|s| !s.trim().is_empty())
            .unwrap_or(false)
            && self.next_run_at.is_none()
        {
            let next = next_cron_occurrence(self.cron.as_deref().unwrap_or(""), Utc::now())?;
            self.next_run_at = Some(next.to_rfc3339());
        }
        if self
            .rrule
            .as_ref()
            .map(|s| !s.trim().is_empty())
            .unwrap_or(false)
            && self.next_run_at.is_none()
        {
            let next = next_rrule_occurrence(self.rrule.as_deref().unwrap_or(""), Utc::now())?;
            self.next_run_at = Some(next.to_rfc3339());
        }
        Ok(())
    }
}
