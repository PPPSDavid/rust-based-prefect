/** Cron preview aligned with the `cron` 0.15 scheduler (seconds field, Sunday = 1). */

export type CronField = {
  values: number[];
};

export type CronFields = {
  second: CronField;
  minute: CronField;
  hour: CronField;
  dom: CronField;
  month: CronField;
  dow: CronField;
  year: CronField;
};

export type CronNormalization = {
  expression: string;
  note: string | null;
  fields: CronFields;
};

type Unit = {
  min: number;
  max: number;
  names?: Record<string, number>;
  allowAny: boolean;
};

const DOW_NAMES: Record<string, number> = {
  sun: 1,
  sunday: 1,
  mon: 2,
  monday: 2,
  tue: 3,
  tues: 3,
  tuesday: 3,
  wed: 4,
  wednesday: 4,
  thu: 5,
  thurs: 5,
  thursday: 5,
  fri: 6,
  friday: 6,
  sat: 7,
  saturday: 7
};

const MONTH_NAMES: Record<string, number> = {
  jan: 1,
  january: 1,
  feb: 2,
  february: 2,
  mar: 3,
  march: 3,
  apr: 4,
  april: 4,
  may: 5,
  jun: 6,
  june: 6,
  jul: 7,
  july: 7,
  aug: 8,
  august: 8,
  sep: 9,
  september: 9,
  oct: 10,
  october: 10,
  nov: 11,
  november: 11,
  dec: 12,
  december: 12
};

const UNITS = {
  second: { min: 0, max: 59, allowAny: false },
  minute: { min: 0, max: 59, allowAny: false },
  hour: { min: 0, max: 23, allowAny: false },
  dom: { min: 1, max: 31, allowAny: true },
  month: { min: 1, max: 12, names: MONTH_NAMES, allowAny: false },
  dow: { min: 1, max: 7, names: DOW_NAMES, allowAny: true },
  year: { min: 1970, max: 2100, allowAny: false }
} as const satisfies Record<string, Unit>;

const SHORTHANDS: Record<string, string> = {
  "@yearly": "0 0 0 1 1 *",
  "@monthly": "0 0 0 1 * *",
  "@weekly": "0 0 0 * * 1",
  "@daily": "0 0 0 * * *",
  "@hourly": "0 0 * * * *"
};

function range(min: number, max: number): number[] {
  const values: number[] = [];
  for (let value = min; value <= max; value += 1) values.push(value);
  return values;
}

function fullField(unit: Unit): CronField {
  return { values: range(unit.min, unit.max) };
}

function parseToken(token: string, unit: Unit, label: string): number[] {
  const stepMatch = /^(.*)\/(\d+)$/.exec(token);
  const step = stepMatch ? Number(stepMatch[2]) : null;
  const base = stepMatch ? stepMatch[1] : token;
  if (step != null && (!Number.isInteger(step) || step < 1 || step > unit.max - unit.min)) {
    throw new Error(`${label} step must be between 1 and ${unit.max - unit.min}.`);
  }
  if (base === "*" || (base === "?" && unit.allowAny)) {
    return step == null ? range(unit.min, unit.max) : stepped(unit.min, unit.max, step);
  }
  if (base === "?" && !unit.allowAny) {
    throw new Error(`${label} does not allow '?'.`);
  }
  const rangeMatch = /^(.+)-(.+)$/.exec(base);
  if (rangeMatch) {
    const start = namedOrNumber(rangeMatch[1], unit, label);
    const end = namedOrNumber(rangeMatch[2], unit, label);
    if (start > end) throw new Error(`${label} range ${base} is reversed.`);
    return step == null ? range(start, end) : stepped(start, end, step);
  }
  const point = namedOrNumber(base, unit, label);
  if (step == null) return [point];
  return stepped(point, unit.max, step);
}

function namedOrNumber(raw: string, unit: Unit, label: string): number {
  if (/^\d+$/.test(raw)) {
    const value = Number(raw);
    if (value < unit.min || value > unit.max) {
      throw new Error(`${label} must be between ${unit.min} and ${unit.max}. ('${raw}' specified.)`);
    }
    return value;
  }
  const named = unit.names?.[raw.toLowerCase()];
  if (named == null) throw new Error(`'${raw}' is not valid for ${label}.`);
  return named;
}

function stepped(start: number, end: number, step: number): number[] {
  const values: number[] = [];
  for (let value = start; value <= end; value += step) values.push(value);
  if (values.length === 0) throw new Error("Cron step produced no values.");
  return values;
}

function parseField(source: string, unit: Unit, label: string): CronField {
  const parts = source.split(",").map((part) => part.trim()).filter(Boolean);
  if (parts.length === 0) throw new Error(`${label} is empty.`);
  const values = new Set<number>();
  for (const part of parts) {
    for (const value of parseToken(part, unit, label)) values.add(value);
  }
  return { values: [...values].sort((a, b) => a - b) };
}

export function parseCron(expression: string): CronFields {
  const compact = expression.trim().replace(/\s+/g, " ");
  const shorthand = SHORTHANDS[compact.toLowerCase()];
  const source = shorthand ?? compact;
  const parts = source.split(" ");
  if (parts.length !== 6 && parts.length !== 7) {
    throw new Error(
      "Cron needs 6 fields (seconds minutes hours day-of-month month day-of-week) or 7 with a year. Example: 0 */10 * * * *."
    );
  }
  try {
    return {
      second: parseField(parts[0], UNITS.second, "Seconds"),
      minute: parseField(parts[1], UNITS.minute, "Minutes"),
      hour: parseField(parts[2], UNITS.hour, "Hours"),
      dom: parseField(parts[3], UNITS.dom, "Day of month"),
      month: parseField(parts[4], UNITS.month, "Month"),
      dow: parseField(parts[5], UNITS.dow, "Day of week"),
      year: parts[6] ? parseField(parts[6], UNITS.year, "Year") : fullField(UNITS.year)
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "Invalid cron expression.";
    throw new Error(message);
  }
}

export function normalizeCronExpression(input: string): CronNormalization {
  const trimmed = input.trim().replace(/\s+/g, " ");
  if (!trimmed) throw new Error("Enter a cron expression.");
  if (trimmed.startsWith("@")) {
    const expression = trimmed.toLowerCase();
    return { expression, note: null, fields: parseCron(expression) };
  }
  const parts = trimmed.split(" ");
  if (parts.length === 5) {
    const expression = `0 ${trimmed}`;
    return {
      expression,
      note: `Five-field cron is saved as ${expression} so the seconds field is 0.`,
      fields: parseCron(expression)
    };
  }
  return { expression: trimmed, note: null, fields: parseCron(trimmed) };
}

function nextAbove(current: number, values: number[]): { value: number; rolled: boolean } {
  for (const value of values) {
    if (value > current) return { value, rolled: false };
  }
  return { value: values[0], rolled: true };
}

function cronDow(date: Date): number {
  const js = date.getUTCDay();
  return js === 0 ? 1 : js + 1;
}

function atUtc(year: number, month: number, day: number, hour: number, minute: number, second: number): Date {
  return new Date(Date.UTC(year, month - 1, day, hour, minute, second, 0));
}

function dayMatches(fields: CronFields, date: Date): boolean {
  return fields.dom.values.includes(date.getUTCDate()) && fields.dow.values.includes(cronDow(date));
}

/** First schedule instant strictly after `after`. Null when nothing falls before year 2101. */
export function nextCronInstant(expression: string, after: Date): Date | null {
  const fields = parseCron(expression);
  const minSecond = fields.second.values[0];
  const minMinute = fields.minute.values[0];
  const minHour = fields.hour.values[0];
  let cursor = new Date(after.getTime() + 1000);
  cursor.setUTCMilliseconds(0);

  for (let guard = 0; guard < 200000; guard += 1) {
    const year = cursor.getUTCFullYear();
    if (year > UNITS.year.max) return null;
    if (!fields.year.values.includes(year)) {
      const nextYear = fields.year.values.find((value) => value > year);
      if (nextYear == null) return null;
      cursor = atUtc(nextYear, 1, 1, minHour, minMinute, minSecond);
      continue;
    }
    const month = cursor.getUTCMonth() + 1;
    if (!fields.month.values.includes(month)) {
      const next = nextAbove(month, fields.month.values);
      const nextYear = next.rolled ? year + 1 : year;
      cursor = atUtc(nextYear, next.value, 1, minHour, minMinute, minSecond);
      continue;
    }
    if (!dayMatches(fields, cursor)) {
      cursor = atUtc(year, month, cursor.getUTCDate() + 1, minHour, minMinute, minSecond);
      continue;
    }
    const hour = cursor.getUTCHours();
    if (!fields.hour.values.includes(hour)) {
      const next = nextAbove(hour, fields.hour.values);
      cursor = next.rolled
        ? atUtc(year, month, cursor.getUTCDate() + 1, next.value, minMinute, minSecond)
        : atUtc(year, month, cursor.getUTCDate(), next.value, minMinute, minSecond);
      continue;
    }
    const minute = cursor.getUTCMinutes();
    if (!fields.minute.values.includes(minute)) {
      const next = nextAbove(minute, fields.minute.values);
      if (next.rolled) {
        cursor = atUtc(year, month, cursor.getUTCDate(), hour + 1, next.value, minSecond);
      } else {
        cursor = atUtc(year, month, cursor.getUTCDate(), hour, next.value, minSecond);
      }
      continue;
    }
    const second = cursor.getUTCSeconds();
    if (!fields.second.values.includes(second)) {
      const next = nextAbove(second, fields.second.values);
      if (next.rolled) {
        cursor = atUtc(year, month, cursor.getUTCDate(), hour, minute + 1, next.value);
      } else {
        cursor = atUtc(year, month, cursor.getUTCDate(), hour, minute, next.value);
      }
      continue;
    }
    return cursor;
  }
  return null;
}
