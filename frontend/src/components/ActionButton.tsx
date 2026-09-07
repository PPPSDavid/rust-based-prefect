import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Button } from "./ui/button";

type ActionButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger";
  children: ReactNode;
};

export function ActionButton({ variant = "secondary", className = "", children, ...rest }: ActionButtonProps) {
  const mapped = variant === "primary" ? "default" : variant === "danger" ? "danger" : "secondary";
  return (
    <Button variant={mapped} className={className} type="button" {...rest}>
      {children}
    </Button>
  );
}
