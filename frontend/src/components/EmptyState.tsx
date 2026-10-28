interface EmptyStateProps {
  title: string;
  detail: string;
}

export function EmptyState({ title, detail }: EmptyStateProps) {
  return <div className="empty-state"><span className="empty-state__mark" aria-hidden="true">—</span><div><h3>{title}</h3><p>{detail}</p></div></div>;
}
