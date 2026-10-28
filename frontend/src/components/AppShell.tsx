import type { ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";

interface AppShellProps {
  children: ReactNode;
}

const comingSoon = ["Documents", "Query", "Evaluations", "Ablations", "Results"];

export function AppShell({ children }: AppShellProps) {
  const location = useLocation();
  const section = location.pathname.startsWith("/jobs") ? "Jobs"
    : location.pathname.startsWith("/artifacts") ? "Artifacts"
      : location.pathname.startsWith("/experiments") ? "Experiments"
      : location.pathname === "/" ? "Overview" : "Not found";

  return (
    <div className="app-frame">
      <aside className="sidebar">
          <NavLink className="brand" to="/" aria-label="RAG Evaluation Workbench overview">
          <span className="brand__glyph" aria-hidden="true"><i /><i /><i /></span>
          <span className="brand__text"><strong>RAG Workbench</strong><small>Evaluation console</small></span>
          </NavLink>
        <div className="sidebar__section-label">WORKSPACE</div>
        <nav aria-label="Main navigation" className="nav-list">
          <NavLink end to="/" title="Overview" className={({ isActive }) => `nav-item${isActive ? " nav-item--active" : ""}`}><span className="nav-icon" aria-hidden="true">⌂</span>Overview</NavLink>
          <NavLink to="/jobs" title="Jobs" className={({ isActive }) => `nav-item${isActive ? " nav-item--active" : ""}`}><span className="nav-icon" aria-hidden="true">≡</span>Jobs</NavLink>
          <NavLink to="/artifacts" title="Artifacts" className={({ isActive }) => `nav-item${isActive ? " nav-item--active" : ""}`}><span className="nav-icon" aria-hidden="true">▧</span>Artifacts</NavLink>
          <NavLink to="/experiments/evaluation" title="Experiments" className={({ isActive }) => `nav-item${isActive ? " nav-item--active" : ""}`}><span className="nav-icon" aria-hidden="true">＋</span>Experiments</NavLink>
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
          <div className="topbar__crumb"><span>Workspace</span><span className="crumb-separator">/</span><strong>{section}</strong></div>
          <span className="topbar__local"><span className="local-mark" />Private local session</span>
        </header>
        <main id="overview" className="main-content">{children}</main>
      </div>
    </div>
  );
}
