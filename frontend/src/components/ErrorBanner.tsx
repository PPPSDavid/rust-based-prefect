import { cn } from "../lib/utils";

type ErrorBannerProps = {
  message: string;
  className?: string;
};

export function ErrorBanner({ message, className }: ErrorBannerProps) {
  return (
    <div
      role="alert"
      className={cn(
        "mb-3 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-foreground",
        className
      )}
    >
      {message}
    </div>
  );
}
