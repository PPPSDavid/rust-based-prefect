import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Boxes,
  Gauge,
  LayoutDashboard,
  Menu,
  Moon,
  Server,
  Sun,
  Workflow,
  X
} from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";
import { NavLink } from "react-router-dom";
import { useTheme } from "../hooks/useTheme";
import { cn } from "../lib/utils";
import { Button } from "./ui/button";
import { Separator } from "./ui/separator";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "./ui/tooltip";

type NavItem = {
  to: string;
  label: string;
  icon: typeof Activity;
  end?: boolean;
};

const NAV_ITEMS: NavItem[] = [
  { to: "/runs", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/runs", label: "Flow Runs", icon: Activity },
  { to: "/flows", label: "Flows", icon: Workflow },
  { to: "/deployments", label: "Deployments", icon: Boxes },
  { to: "/work-pools", label: "Work Pools", icon: Server },
  { to: "/concurrency", label: "Concurrency", icon: Gauge }
];

type AppShellProps = {
  children: ReactNode;
};

const base = import.meta.env.VITE_API_BASE ?? "";

async function fetchHealth(): Promise<"ok" | "down"> {
  try {
    const res = await fetch(`${base}/health`);
    if (!res.ok) return "down";
    return "ok";
  } catch {
    return "down";
  }
}

export function AppShell({ children }: AppShellProps) {
  const { mode, cycle, resolved } = useTheme();
  const [mobileOpen, setMobileOpen] = useState(false);
  const health = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 15_000,
    staleTime: 10_000
  });

  const nav = (
    <nav className="flex flex-1 flex-col gap-1 p-2" aria-label="Primary">
      {NAV_ITEMS.map((item) => {
        const Icon = item.icon;
        return (
          <NavLink
            key={`${item.label}-${item.to}`}
            to={item.to}
            end={item.end}
            onClick={() => setMobileOpen(false)}
            title={item.label === "Dashboard" ? "Dashboard (U2) — opens Flow Runs" : item.label}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-sidebar-foreground transition-colors hover:bg-sidebar-active",
                isActive && "bg-sidebar-active text-foreground"
              )
            }
          >
            <Icon className="h-4 w-4 shrink-0" aria-hidden />
            <span className="hidden truncate lg:inline">{item.label}</span>
          </NavLink>
        );
      })}
    </nav>
  );

  const themeLabel = mode === "system" ? `System (${resolved})` : mode === "light" ? "Light" : "Dark";

  return (
    <TooltipProvider delayDuration={200}>
      <div className="flex min-h-screen bg-background text-foreground">
        <aside className="sticky top-0 z-30 hidden h-screen w-14 shrink-0 flex-col border-r border-sidebar-border bg-sidebar lg:w-56 md:flex">
          <div className="flex h-14 items-center gap-2 px-3">
            <span className="flex h-8 w-8 items-center justify-center rounded-md bg-accent text-sm font-bold text-accent-foreground">
              IF
            </span>
            <span className="hidden truncate text-sm font-semibold lg:inline">IronFlow</span>
          </div>
          <Separator />
          {nav}
        </aside>

        {mobileOpen ? (
          <div className="fixed inset-0 z-40 md:hidden">
            <button
              type="button"
              className="absolute inset-0 bg-black/50"
              aria-label="Close navigation"
              onClick={() => setMobileOpen(false)}
            />
            <aside className="absolute inset-y-0 left-0 flex w-64 flex-col bg-sidebar shadow-xl">
              <div className="flex h-14 items-center justify-between px-3">
                <span className="text-sm font-semibold">IronFlow</span>
                <Button variant="ghost" size="icon" onClick={() => setMobileOpen(false)} aria-label="Close menu">
                  <X className="h-4 w-4" />
                </Button>
              </div>
              <Separator />
              <nav className="flex flex-1 flex-col gap-1 p-2" aria-label="Primary">
                {NAV_ITEMS.map((item) => {
                  const Icon = item.icon;
                  return (
                    <NavLink
                      key={`m-${item.label}`}
                      to={item.to}
                      end={item.end}
                      onClick={() => setMobileOpen(false)}
                      className={({ isActive }) =>
                        cn(
                          "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-sidebar-foreground hover:bg-sidebar-active",
                          isActive && "bg-sidebar-active text-foreground"
                        )
                      }
                    >
                      <Icon className="h-4 w-4 shrink-0" aria-hidden />
                      <span>{item.label}</span>
                    </NavLink>
                  );
                })}
              </nav>
            </aside>
          </div>
        ) : null}

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border bg-surface/90 px-4 backdrop-blur">
            <Button
              variant="ghost"
              size="icon"
              className="md:hidden"
              onClick={() => setMobileOpen(true)}
              aria-label="Open menu"
            >
              <Menu className="h-4 w-4" />
            </Button>
            <div className="flex flex-1 items-center gap-2">
              <h1 className="text-sm font-semibold md:hidden">IronFlow</h1>
            </div>
            <div className="flex items-center gap-2">
              <span
                className="inline-flex items-center gap-1.5 rounded-full border border-border px-2 py-0.5 text-xs text-muted"
                title="API health"
              >
                <span
                  className={cn(
                    "h-2 w-2 rounded-full",
                    health.data === "ok"
                      ? "bg-state-online"
                      : health.isLoading
                        ? "bg-state-pending"
                        : "bg-state-failed"
                  )}
                  aria-hidden
                />
                <span className="sr-only">Server status:</span>
                {health.data === "ok" ? "API online" : health.isLoading ? "Checking…" : "API offline"}
              </span>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    type="button"
                    onClick={(e) => {
                      e.preventDefault();
                      cycle();
                    }}
                    aria-label={`Theme: ${themeLabel}`}
                  >
                    {resolved === "dark" ? <Moon className="h-4 w-4" /> : <Sun className="h-4 w-4" />}
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Theme: {themeLabel} (click to toggle)</TooltipContent>
              </Tooltip>
            </div>
          </header>
          <main className="mx-auto w-full max-w-screen-2xl flex-1 p-4 md:p-6">{children}</main>
        </div>
      </div>
    </TooltipProvider>
  );
}
