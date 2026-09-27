export type SortDir = "asc" | "desc";

export type SortSpec = {
  key: string;
  dir: SortDir;
};

export function readQuery(params: URLSearchParams): string {
  return params.get("q") ?? "";
}

export function writeQuery(params: URLSearchParams, query: string): void {
  const text = query.trim();
  if (text) params.set("q", text);
  else params.delete("q");
}

export function readChoice(
  params: URLSearchParams,
  key: string,
  allowed: readonly string[],
  fallback: string
): string {
  const raw = params.get(key);
  if (raw && allowed.includes(raw)) return raw;
  return fallback;
}

export function writeChoice(params: URLSearchParams, key: string, value: string, fallback: string): void {
  if (value === fallback) params.delete(key);
  else params.set(key, value);
}

export function readSort(
  params: URLSearchParams,
  allowed: readonly string[],
  fallback: SortSpec
): SortSpec {
  const key = params.get("sort");
  if (!key || !allowed.includes(key)) return fallback;
  const dir = params.get("dir");
  if (dir !== "asc" && dir !== "desc") return { key, dir: fallback.dir };
  return { key, dir };
}

export function writeSort(params: URLSearchParams, sort: SortSpec, fallback: SortSpec): void {
  if (sort.key === fallback.key && sort.dir === fallback.dir) {
    params.delete("sort");
    params.delete("dir");
    return;
  }
  params.set("sort", sort.key);
  params.set("dir", sort.dir);
}

export function encodeSort(sort: SortSpec): string {
  return `${sort.key}:${sort.dir}`;
}

export function decodeSort(value: string, allowed: readonly string[], fallback: SortSpec): SortSpec {
  const splitAt = value.lastIndexOf(":");
  if (splitAt <= 0) return fallback;
  const key = value.slice(0, splitAt);
  const dir = value.slice(splitAt + 1);
  if (!allowed.includes(key)) return fallback;
  if (dir !== "asc" && dir !== "desc") return { key, dir: fallback.dir };
  return { key, dir };
}

export function filterByText<T>(rows: T[], query: string, text: (row: T) => string): T[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return rows;
  return rows.filter((row) => text(row).toLowerCase().includes(needle));
}

export function sortRows<T>(rows: T[], sort: SortSpec, valueOf: (row: T, key: string) => string | number): T[] {
  if (sort.key === "recent") {
    return sort.dir === "asc" ? [...rows].reverse() : [...rows];
  }
  const decorated = rows.map((row, index) => ({ row, index }));
  decorated.sort((left, right) => {
    const delta = compareValues(valueOf(left.row, sort.key), valueOf(right.row, sort.key));
    if (delta !== 0) return sort.dir === "asc" ? delta : -delta;
    return left.index - right.index;
  });
  return decorated.map((item) => item.row);
}

function compareValues(left: string | number, right: string | number): number {
  if (typeof left === "number" && typeof right === "number") return left - right;
  return String(left).localeCompare(String(right), undefined, { numeric: true, sensitivity: "base" });
}
