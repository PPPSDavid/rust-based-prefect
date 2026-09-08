import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../../lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60",
  {
    variants: {
      variant: {
        default: "bg-accent text-accent-foreground hover:opacity-90",
        secondary: "bg-surface-muted text-foreground border border-border hover:bg-surface",
        danger: "bg-danger text-danger-foreground hover:opacity-90",
        ghost: "hover:bg-surface-muted text-foreground",
        outline: "border border-border bg-transparent hover:bg-surface-muted"
      },
      size: {
        default: "h-9 px-3 py-2",
        sm: "h-8 rounded-md px-2.5 text-xs",
        lg: "h-10 rounded-md px-4",
        icon: "h-9 w-9"
      }
    },
    defaultVariants: {
      variant: "default",
      size: "default"
    }
  }
);

export type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean;
  };

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />;
  }
);
Button.displayName = "Button";
