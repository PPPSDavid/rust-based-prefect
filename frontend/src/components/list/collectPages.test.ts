import { describe, expect, it, vi } from "vitest";
import { collectPages } from "./collectPages";

describe("collectPages", () => {
  it("follows the cursor until the list is complete", async () => {
    const load = vi
      .fn()
      .mockResolvedValueOnce({ items: [{ id: "a" }], next_cursor: "c1" })
      .mockResolvedValueOnce({ items: [{ id: "b" }], next_cursor: null });
    await expect(collectPages(load)).resolves.toEqual([{ id: "a" }, { id: "b" }]);
    expect(load).toHaveBeenNthCalledWith(1, undefined);
    expect(load).toHaveBeenNthCalledWith(2, "c1");
  });

  it("stops when a cursor repeats", async () => {
    const load = vi.fn().mockResolvedValue({ items: [{ id: "a" }], next_cursor: "c1" });
    await expect(collectPages(load)).resolves.toEqual([{ id: "a" }, { id: "a" }]);
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("stops after a bounded number of pages", async () => {
    let n = 0;
    const load = vi.fn(async () => {
      n += 1;
      return { items: [{ id: String(n) }], next_cursor: `c${n}` };
    });
    const items = await collectPages(load);
    expect(load).toHaveBeenCalledTimes(50);
    expect(items).toHaveLength(50);
  });
});
