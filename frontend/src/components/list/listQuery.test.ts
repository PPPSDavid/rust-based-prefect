import { describe, expect, it } from "vitest";
import {
  decodeSort,
  encodeSort,
  filterByText,
  readChoice,
  readQuery,
  readSort,
  sortRows,
  writeChoice,
  writeQuery,
  writeSort
} from "./listQuery";

describe("list query URL state", () => {
  it("omits default search, status, and sort", () => {
    const params = new URLSearchParams("q=keep&status=archived&sort=name&dir=asc");
    writeQuery(params, "  ");
    writeChoice(params, "status", "active", "active");
    writeSort(params, { key: "updated", dir: "desc" }, { key: "updated", dir: "desc" });
    expect(params.toString()).toBe("");
  });

  it("round-trips a non-default view", () => {
    const params = new URLSearchParams();
    writeQuery(params, " hourly ");
    writeChoice(params, "status", "paused", "all");
    writeSort(params, { key: "name", dir: "asc" }, { key: "recent", dir: "desc" });
    expect(readQuery(params)).toBe("hourly");
    expect(readChoice(params, "status", ["all", "paused"], "all")).toBe("paused");
    expect(readSort(params, ["recent", "name"], { key: "recent", dir: "desc" })).toEqual({
      key: "name",
      dir: "asc"
    });
    expect(encodeSort({ key: "name", dir: "asc" })).toBe("name:asc");
  });

  it("ignores unknown sort and status values", () => {
    const params = new URLSearchParams("status=nope&sort=nope&dir=sideways");
    expect(readChoice(params, "status", ["all", "active"], "all")).toBe("all");
    expect(readSort(params, ["recent", "name"], { key: "recent", dir: "desc" })).toEqual({
      key: "recent",
      dir: "desc"
    });
    expect(decodeSort("nope:asc", ["name"], { key: "recent", dir: "desc" })).toEqual({
      key: "recent",
      dir: "desc"
    });
  });
});

describe("list query sort", () => {
  it("keeps recent order and sorts names", () => {
    const rows = [{ name: "b" }, { name: "a" }];
    expect(sortRows(rows, { key: "recent", dir: "desc" }, (row) => row.name).map((row) => row.name)).toEqual([
      "b",
      "a"
    ]);
    expect(sortRows(rows, { key: "name", dir: "asc" }, (row) => row.name).map((row) => row.name)).toEqual([
      "a",
      "b"
    ]);
  });

  it("filters by the provided text", () => {
    const rows = [{ name: "alpha" }, { name: "beta" }];
    expect(filterByText(rows, "ALP", (row) => row.name)).toEqual([{ name: "alpha" }]);
  });
});
