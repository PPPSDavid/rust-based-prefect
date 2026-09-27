import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { Deployment } from "../../types";
import { EmptyState } from "../EmptyState";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../ui/table";
import { deploymentCountLabel, formatSchedule, type DeploymentGroup } from "./deploymentGroups";

type DeploymentGroupTableProps = {
  groups: DeploymentGroup[];
  collapsed: ReadonlySet<string>;
  onToggle: (flowName: string) => void;
  emptyMessage: string;
  renderActions: (deployment: Deployment) => ReactNode;
};

export function DeploymentGroupTable({
  groups,
  collapsed,
  onToggle,
  emptyMessage,
  renderActions
}: DeploymentGroupTableProps) {
  if (groups.length === 0) {
    return <EmptyState title="Nothing here" message={emptyMessage} />;
  }

  return (
    <Table className="grid-table">
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead>Schedule</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {groups.map((group) => {
          return (
            <FlowGroupRows
              key={group.flowName}
              group={group}
              open={!collapsed.has(group.flowName)}
              onToggle={onToggle}
              renderActions={renderActions}
            />
          );
        })}
      </TableBody>
    </Table>
  );
}

function FlowGroupRows({
  group,
  open,
  onToggle,
  renderActions
}: {
  group: DeploymentGroup;
  open: boolean;
  onToggle: (flowName: string) => void;
  renderActions: (deployment: Deployment) => ReactNode;
}) {
  const rowIds = group.deployments.map((dep) => `deployment-row-${dep.id}`);
  return (
    <>
      <TableRow className="flow-group-row">
        <TableCell colSpan={4}>
          <button
            type="button"
            className="flow-group-toggle"
            aria-expanded={open}
            aria-controls={open ? rowIds.join(" ") : undefined}
            aria-label={`Toggle deployments for ${group.flowName}`}
            onClick={() => onToggle(group.flowName)}
          >
            <span aria-hidden="true">{open ? "▾" : "▸"}</span>
          </button>
          <Link to={`/flows/${encodeURIComponent(group.flowName)}`}>{group.flowName}</Link>
          <span className="flow-group-count">{deploymentCountLabel(group.deployments.length)}</span>
        </TableCell>
      </TableRow>
      {open
        ? group.deployments.map((dep, index) => (
            <TableRow key={dep.id} id={rowIds[index]}>
              <TableCell className="list-group-child">
                <Link to={`/deployments/${dep.id}`}>{dep.name}</Link>
              </TableCell>
              <TableCell>{formatSchedule(dep)}</TableCell>
              <TableCell>{dep.paused ? "Paused" : "Active"}</TableCell>
              <TableCell>{renderActions(dep)}</TableCell>
            </TableRow>
          ))
        : null}
    </>
  );
}
