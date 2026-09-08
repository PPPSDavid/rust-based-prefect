import { cn } from "../lib/utils";

type EmptyStateProps = {
  title: string;
  message?: string;
  className?: string;
};

export function EmptyState({ title, message, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        "rounded-lg border border-dashed border-border bg-surface-muted/40 px-6 py-10 text-center",
        className
      )}
    >
      <h3 className="m-0 text-base font-semibold text-foreground">{title}</h3>
      {message ? <p className="mt-2 mb-0 text-sm text-muted">{message}</p> : null}
    </div>
  );
}
