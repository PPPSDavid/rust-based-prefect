import { describe, expect, it } from "vitest";
import { nextCronInstant, normalizeCronExpression } from "./cron";

function at(iso: string): Date {
  return new Date(iso);
}

function next(expression: string, after: string): string | null {
  return nextCronInstant(expression, at(after))?.toISOString() ?? null;
}

describe("cron preview", () => {
  it("matches the Rust scheduler for common expressions", () => {
    expect(next("0 */10 * * * *", "2026-09-27T12:00:30Z")).toBe("2026-09-27T12:10:00.000Z");
    expect(next("0 */10 * * * *", "2026-09-27T12:10:00Z")).toBe("2026-09-27T12:20:00.000Z");
    expect(next("0 * * * * *", "2026-09-27T12:00:30Z")).toBe("2026-09-27T12:01:00.000Z");
    expect(next("0 0 9 * * *", "2026-09-27T12:00:30Z")).toBe("2026-09-28T09:00:00.000Z");
    expect(next("0 15,45 * * * *", "2026-09-27T12:10:00Z")).toBe("2026-09-27T12:15:00.000Z");
    expect(next("30 */15 * * * *", "2026-09-27T12:00:30Z")).toBe("2026-09-27T12:15:30.000Z");
    expect(next("0 0 0 31 * *", "2026-01-01T00:00:00Z")).toBe("2026-01-31T00:00:00.000Z");
    expect(next("0 0 0 31 2 *", "2026-01-01T00:00:00Z")).toBeNull();
    expect(next("@hourly", "2026-09-27T12:00:30Z")).toBe("2026-09-27T13:00:00.000Z");
    expect(next("@hourly", "2026-09-27T13:00:00Z")).toBe("2026-09-27T14:00:00.000Z");
    expect(next("@daily", "2026-09-27T12:00:30Z")).toBe("2026-09-28T00:00:00.000Z");
    expect(next("@weekly", "2026-09-27T12:00:30Z")).toBe("2026-10-04T00:00:00.000Z");
    expect(next("@monthly", "2026-09-27T12:00:30Z")).toBe("2026-10-01T00:00:00.000Z");
    expect(next("@yearly", "2026-09-27T12:00:30Z")).toBe("2027-01-01T00:00:00.000Z");
    expect(next("0 0 9 * * MON-FRI", "2026-09-27T12:00:30Z")).toBe("2026-09-28T09:00:00.000Z");
    expect(next("0 0 9 * * 1", "2026-09-27T12:00:30Z")).toBe("2026-10-04T09:00:00.000Z");
    expect(next("0 0 * * * 1", "2026-09-27T12:00:30Z")).toBe("2026-09-27T13:00:00.000Z");
    expect(next("0 0 * * * SUN", "2026-09-27T12:00:30Z")).toBe("2026-09-27T13:00:00.000Z");
    expect(next("0 0 * * * 7", "2026-09-27T12:00:30Z")).toBe("2026-10-03T00:00:00.000Z");
    expect(next("0 0 0 ? * MON", "2026-09-27T12:00:30Z")).toBe("2026-09-28T00:00:00.000Z");
    expect(next("0 0 12 * * ?", "2026-09-27T12:00:00Z")).toBe("2026-09-28T12:00:00.000Z");
    expect(next("0 0 0 1 * SUN", "2026-01-01T00:00:00Z")).toBe("2026-02-01T00:00:00.000Z");
    expect(next("1-5/2 * * * * *", "2026-09-27T12:00:00Z")).toBe("2026-09-27T12:00:01.000Z");
    expect(next("0 30 9,12,15 1,15 May-Aug Mon,Wed,Fri 2018/2", "2018-05-01T00:00:00Z")).toBe(
      "2018-06-01T09:30:00.000Z"
    );
  });

  it("rejects a seconds-less expression until a seconds field is added", () => {
    expect(() => normalizeCronExpression("*/10 * * * *")).not.toThrow();
    expect(normalizeCronExpression("*/10 * * * *")).toMatchObject({
      expression: "0 */10 * * * *",
      note: expect.stringContaining("0 */10 * * * *")
    });
    expect(() => normalizeCronExpression("0 0 * * * 0")).toThrow(/Day of week/);
  });
});