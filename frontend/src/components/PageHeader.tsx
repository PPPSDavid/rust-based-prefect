import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { cn } from "../lib/utils";

type Breadcrumb = {
  label: string;
  to?: string;
};

type PageHeaderProps = {
  title: string;
  subtitle?: string;
  breadcrumbs?: Breadcrumb[];
  actions?: ReactNode;
  className?: string;
};

export function PageHeader({ title, subtitle, breadcrumbs, actions, className }: PageHeaderProps) {
  return (
    <header className={cn("mb-4", className)}>
      {breadcrumbs && breadcrumbs.length > 0 ? (
        <nav className="mb-2 text-sm text-muted" aria-label="Breadcrumb">
          {breadcrumbs.map((crumb, index) => (
            <span key={`${crumb.label}-${index}`}>
              {index > 0 ? <span className="text-muted/70"> / </span> : null}
              {crumb.to ? <Link to={crumb.to}>{crumb.label}</Link> : <span>{crumb.label}</span>}
            </span>
          ))}
        </nav>
      ) : null}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="m-0 text-xl font-semibold tracking-tight">{title}</h2>
          {subtitle ? <p className="mt-1 mb-0 text-sm text-muted">{subtitle}</p> : null}
        </div>
        {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
      </div>
    </header>
  );
}
