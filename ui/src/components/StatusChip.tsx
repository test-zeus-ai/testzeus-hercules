import { Icon, type IconName } from "./Icon";

const LABELS: Record<string, string> = {
  queued: "Queued",
  starting: "Starting",
  running: "Running",
  paused: "Paused",
  needs_approval: "Needs approval",
  needs_plan_approval: "Plan to review",
  passed: "Passed",
  failed: "Failed",
  error: "Error",
  cancelled: "Cancelled",
  pending: "Pending",
  recovering: "Retrying",
  skipped: "Skipped",
  blocked: "Blocked",
  unverified: "Unverified",
};

const ICONS: Record<string, IconName> = {
  passed: "check",
  failed: "cross",
  error: "alert",
  blocked: "alert",
  cancelled: "stop",
  paused: "pause",
};

/** A status pill; colour is never the only signal (icon and text carry it too). */
export function StatusChip({ status, compact = false }: { status: string; compact?: boolean }) {
  const label = LABELS[status] ?? status;
  const icon = ICONS[status];
  return (
    <span className={`chip chip-${status}${compact ? " chip-compact" : ""}`} title={label}>
      {icon ? <Icon name={icon} size={12} /> : <span className="chip-dot" aria-hidden="true" />}
      {compact ? <span className="visually-hidden">{label}</span> : label}
    </span>
  );
}
