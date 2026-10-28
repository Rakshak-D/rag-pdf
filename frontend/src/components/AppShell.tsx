import type { ReactNode } from "react";

interface AppShellProps {
  children: ReactNode;
}

const comingSoon = ["Documents", "Query", "Evaluations", "Ablations", "Results"];

export function AppShell({ children }: AppShellProps) {
  return (
    <div className="app-frame">
      <aside className="sidebar">
        <a className="brand" href="#overview" aria-label="RAG Evaluation Workbench overview">
          <span className="brand__glyph" aria-hidden="true"><i /><i /><i /></span>
          <span className="brand__text"><strong>RAG Workbench</strong><small>Evaluation console</small></span>
        </a>
        <div className="sidebar__section-label">WORKSPACE</div>
        <nav aria-label="Main navigation" className="nav-list">
          <a className="nav-item nav-item--active" href="#overview" aria-current="page"><span className="nav-icon" aria-hidden="true">⌂</span>Overview</a>
          {comingSoon.map((item, index) => (
            <div className="nav-item nav-item--disabled" key={item} aria-disabled="true" title={`${item} is not available yet`}>
              <span className="nav-icon" aria-hidden="true">{["▤", "⌕", "◷", "⇄", "▧"][index]}</span>
              <span>{item}</span><span className="nav-soon">Next</span>
            </div>
          ))}
        </nav>
        <div className="sidebar__footer"><span className="local-mark" />Local runtime <span className="sidebar__footer-version">API v1</span></div>
      </aside>
      <div className="app-content">
        <header className="topbar">
          <div className="topbar__crumb"><span>Workspace</span><span className="crumb-separator">/</span><strong>Overview</strong></div>
          <span className="topbar__local"><span className="local-mark" />Private local session</span>
        </header>
        <main id="overview" className="main-content">{children}</main>
      </div>
    </div>
  );
}
