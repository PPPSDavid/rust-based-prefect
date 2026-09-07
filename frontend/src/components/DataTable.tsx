import type { ReactNode } from "react";
import { EmptyState } from "./EmptyState";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "./ui/table";

export type Column<T> = {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
};

type DataTableProps<T> = {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  emptyMessage?: string;
};

export function DataTable<T>({ columns, rows, rowKey, emptyMessage = "No items found." }: DataTableProps<T>) {
  if (rows.length === 0) {
    return <EmptyState title="Nothing here" message={emptyMessage} />;
  }

  return (
    <Table className="grid-table">
      <TableHeader>
        <TableRow>
          {columns.map((col) => (
            <TableHead key={col.key}>{col.header}</TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={rowKey(row)}>
            {columns.map((col) => (
              <TableCell key={col.key}>{col.render(row)}</TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
