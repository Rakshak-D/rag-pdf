interface ErrorStateProps {
  title: string;
  detail: string;
  onRetry?: () => void;
}

export function ErrorState({ title, detail, onRetry }: ErrorStateProps) {
  return (
    <div className="error-state" role="alert">
      <span className="error-state__icon" aria-hidden="true">!</span>
      <div><h3>{title}</h3><p>{detail}</p></div>
      {onRetry ? <button className="button button--quiet button--small" onClick={onRetry}>Retry</button> : null}
    </div>
  );
}
