import type { ReactNode } from "react";
import { ErrorState } from "../components/ErrorState";
import { EmptyState } from "../components/EmptyState";

export function PageHeading({ eyebrow, title, description, actions }: {
  eyebrow: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return <div className="page-heading"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p className="page-heading__description">{description}</p></div>{actions}</div>;
}

export function LoadingState({ label }: { label: string }) {
  return <div className="loading-state" role="status"><span className="loading-spinner" aria-hidden="true" />Loading {label}â€¦</div>;
}

export function ProtectedState({ connected, loading, error, empty, emptyDetail, retry }: {
  connected: boolean;
  loading: boolean;
  error: string | null;
  empty?: boolean;
  emptyDetail?: string;
  retry: () => void;
}) {
  if (!connected) return <EmptyState title="Connect to view this page" detail="Enter the local API bearer token above to load protected records." />;
  if (loading) return <LoadingState label="records" />;
  if (error) return <ErrorState title="Records could not be loaded" detail={error} onRetry={retry} />;
  if (empty) return <EmptyState title="No matching records" detail={emptyDetail ?? "Clear filters or check again later."} />;
  return null;
}

export function formatTimestamp(value: string | null): string {
  if (value === null) return "Not recorded";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function displayValue(value: string | number | null | undefined): string {
  return value === null || value === undefined || value === "" ? "â€”" : String(value);
}

export function Pagination({ offset, limit, count, hasMore, onPrevious, onNext }: {
  offset: number;
  limit: number;
  count: number;
  hasMore: boolean;
  onPrevious: () => void;
  onNext: () => void;
}) {
  const page = Math.floor(offset / limit) + 1;
  return <nav className="pagination" aria-label="Result pages"><button className="button button--quiet" type="button" onClick={onPrevious} disabled={offset === 0}>Previous</button><span role="status">Page {page} Â· {count} shown</span><button className="button button--quiet" type="button" onClick={onNext} disabled={!hasMore}>Next</button></nav>;
}
