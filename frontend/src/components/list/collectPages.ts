import type { CursorPage } from "../../types";

const MAX_PAGES = 50;

/**
 * Follow cursors until the API reports no further page.
 * Text search and sort then run on the full list, so a match on a later
 * page is not dropped. A repeat cursor or the page cap ends the walk.
 */
export async function collectPages<T>(load: (cursor?: string) => Promise<CursorPage<T>>): Promise<T[]> {
  const items: T[] = [];
  const seenCursors = new Set<string>();
  let cursor: string | undefined;
  for (let pageIndex = 0; pageIndex < MAX_PAGES; pageIndex += 1) {
    const page = await load(cursor);
    items.push(...page.items);
    const next = page.next_cursor;
    if (!next || seenCursors.has(next)) return items;
    seenCursors.add(next);
    cursor = next;
  }
  return items;
}
