import { cn } from "../lib/utils";
import { Badge } from "./ui/badge";

type StateBadgeProps = {
  state: string;
  className?: string;
};

const STATE_VARIANTS = new Set([
  "running",
  "completed",
  "failed",
  "cancelled",
  "pending",
  "scheduled",
  "paused",
  "crashed",
  "claimed",
  "online"
]);

export function StateBadge({ state, className }: StateBadgeProps) {
  const normalized = state.toLowerCase().replace(/_/g, "-");
  const variant = STATE_VARIANTS.has(normalized)
    ? (normalized as
        | "running"
        | "completed"
        | "failed"
        | "cancelled"
        | "pending"
        | "scheduled"
        | "paused"
        | "crashed"
        | "claimed"
        | "online")
    : "default";
  return (
    <Badge variant={variant} className={cn(className)}>
      {state}
    </Badge>
  );
}
