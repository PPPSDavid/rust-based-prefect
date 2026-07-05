type StateBadgeProps = {
  state: string;
};

export function StateBadge({ state }: StateBadgeProps) {
  const normalized = state.toLowerCase().replace(/_/g, "-");
  return <span className={`badge badge-${normalized}`}>{state}</span>;
}
