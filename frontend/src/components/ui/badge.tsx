import type { HTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../../lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium",
  {
    variants: {
      variant: {
        default: "border-transparent bg-surface-muted text-foreground",
        outline: "border-border text-foreground",
        running: "border-transparent bg-state-running text-white",
        completed: "border-transparent bg-state-completed text-white",
        failed: "border-transparent bg-state-failed text-white",
        cancelled: "border-transparent bg-state-cancelled text-white",
        pending: "border-transparent bg-state-pending text-white",
        scheduled: "border-transparent bg-state-scheduled text-white",
        paused: "border-transparent bg-state-paused text-white",
        crashed: "border-transparent bg-state-crashed text-white",
        claimed: "border-transparent bg-state-claimed text-white",
        online: "border-transparent bg-state-online text-white"
      }
    },
    defaultVariants: {
      variant: "default"
    }
  }
);

export type BadgeProps = HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>;

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
