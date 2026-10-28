interface StatusBadgeProps {
  status: string;
  label?: string;
}

function tone(status: string): string {
  if (["ready", "succeeded", "ok"].includes(status)) return "good";
  if (["partial", "loading", "queued", "running", "unloaded"].includes(status)) return "working";
  if (["failed", "cancelled", "interrupted"].includes(status)) return "bad";
  return "neutral";
}

export function StatusBadge({ status, label }: StatusBadgeProps) {
  return <span className={`status-badge status-badge--${tone(status)}`}><span className="status-dot" aria-hidden="true" />{label ?? status}</span>;
}
