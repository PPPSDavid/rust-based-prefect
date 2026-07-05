import type { ButtonHTMLAttributes, ReactNode } from "react";

type ActionButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger";
  children: ReactNode;
};

export function ActionButton({ variant = "secondary", className = "", children, ...rest }: ActionButtonProps) {
  return (
    <button className={`btn btn-${variant} ${className}`.trim()} type="button" {...rest}>
      {children}
    </button>
  );
}
