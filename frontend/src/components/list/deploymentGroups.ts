import type { Deployment } from "../../types";
import { sortRows, type SortSpec } from "./listQuery";

export type DeploymentStatusFilter = "all" | "active" | "paused";

export type DeploymentGroup = {
  flowName: string;
  deployments: Deployment[];
};

export function formatSchedule(
  dep: Pick<Deployment, "schedule_enabled" | "schedule_cron" | "schedule_rrule" | "schedule_interval_seconds">
): string {
  if (!dep.schedule_enabled) return "Manual";
  if (dep.schedule_cron?.trim()) return `cron ${dep.schedule_cron}`;
  if (dep.schedule_rrule?.trim()) return `rrule ${dep.schedule_rrule}`;
  if (dep.schedule_interval_seconds != null) return `every ${dep.schedule_interval_seconds}s`;
  return "Scheduled";
}

export function filterDeployments(
  items: Deployment[],
  query: string,
  status: DeploymentStatusFilter
): Deployment[] {
  const byStatus = items.filter((dep) => matchesStatus(dep, status));
  const needle = query.trim().toLowerCase();
  if (!needle) return byStatus;
  const flowsMatchingName = new Set(
    byStatus.filter((dep) => dep.flow_name.toLowerCase().includes(needle)).map((dep) => dep.flow_name)
  );
  return byStatus.filter(
    (dep) => flowsMatchingName.has(dep.flow_name) || dep.name.toLowerCase().includes(needle)
  );
}

export function groupDeployments(items: Deployment[], sort: SortSpec): DeploymentGroup[] {
  const ordered =
    sort.key === "flow"
      ? sortRows(items, { key: "flow", dir: sort.dir }, (dep) => dep.flow_name)
      : sortRows(items, sort, deploymentSortValue);
  const groups: DeploymentGroup[] = [];
  const byFlow = new Map<string, DeploymentGroup>();
  for (const dep of ordered) {
    const flowName = dep.flow_name.trim() || "Unknown flow";
    let group = byFlow.get(flowName);
    if (!group) {
      group = { flowName, deployments: [] };
      byFlow.set(flowName, group);
      groups.push(group);
    }
    group.deployments.push(dep);
  }
  if (sort.key === "flow") {
    for (const group of groups) {
      group.deployments.sort((left, right) =>
        left.name.localeCompare(right.name, undefined, { sensitivity: "base" })
      );
    }
  }
  return groups;
}

export function deploymentCountLabel(count: number): string {
  return count === 1 ? "1 deployment" : `${count} deployments`;
}

function matchesStatus(dep: Deployment, status: DeploymentStatusFilter): boolean {
  if (status === "active") return !dep.paused;
  if (status === "paused") return dep.paused;
  return true;
}

function deploymentSortValue(dep: Deployment, key: string): string | number {
  if (key === "name") return dep.name;
  if (key === "updated") return Date.parse(dep.updated_at) || 0;
  return dep.flow_name;
}
