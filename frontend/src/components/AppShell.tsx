import type { ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";

interface AppShellProps {
  children: ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  const location = useLocation();
  const section = location.pathname.startsWith("/jobs") ? "Jobs"
    : location.pathname.startsWith("/artifacts") ? "Results"
      : location.pathname.startsWith("/index-builds") ? "Index Builds"
      : location.pathname.startsWith("/documents") ? "Documents"
      : location.pathname.startsWith("/ask") ? "Ask"
      : location.pathname.startsWith("/experiments/ablation") ? "Ablations"
        : location.pathname.startsWith("/experiments/evaluation") ? "Evaluations"
      : location.pathname === "/" ? "Overview" : "Not found";

  return (
    <div className="app-frame">
      <aside className="sidebar">
          <NavLink className="brand" to="/" aria-label="RAG Workbench overview">
          <span className="brand__glyph" aria-hidden="true"><i /><i /><i /></span>
          <span className="brand__text"><strong>RAG Workbench</strong><small>Local workspace</small></span>
          </NavLink>
        <div className="sidebar__section-label">WORKSPACE</div>
        <nav aria-label="Main navigation" className="nav-list">
          <NavLink end to="/" title="Overview" className={({ isActive }) => `nav-item${isActive ? " nav-item--active" : ""}`}><span className="nav-icon" aria-hidden="true">⌂</span>Overview</NavLink>
          <NavLink to="/jobs" title="Jobs" className={({ isActive }) => `nav-item${isActive ? " nav-item--active" : ""}`}><span className="nav-icon" aria-hidden="true">≡</span>Jobs</NavLink>
          <NavLink to="/index-builds" title="Index Builds" className={({ isActive }) => `nav-item${isActive ? " nav-item--active" : ""}`}><span className="nav-icon" aria-hidden="true">⌁</span>Index Builds</NavLink>
          <NavLink to="/documents" title="Documents" className={({ isActive }) => `nav-item${isActive ? " nav-item--active" : ""}`}><span className="nav-icon" aria-hidden="true">▤</span>Documents</NavLink>
          <NavLink to="/ask" title="Ask" className={({ isActive }) => `nav-item${isActive ? " nav-item--active" : ""}`}><span className="nav-icon" aria-hidden="true">⌕</span>Ask</NavLink>
        <div className="sidebar__section-label sidebar__section-label--research">RESEARCH</div>
          <NavLink end to="/experiments/evaluation" title="Evaluations" className={({ isActive }) => `nav-item${isActive ? " nav-item--active" : ""}`}><span className="nav-icon" aria-hidden="true">＋</span>Evaluations</NavLink>
          <NavLink end to="/experiments/ablation" title="Ablations" className={({ isActive }) => `nav-item${isActive ? " nav-item--active" : ""}`}><span className="nav-icon" aria-hidden="true">⇄</span>Ablations</NavLink>
          <NavLink to="/artifacts" title="Results" className={({ isActive }) => `nav-item${isActive ? " nav-item--active" : ""}`}><span className="nav-icon" aria-hidden="true">▧</span>Results</NavLink>
        </nav>
        <div className="sidebar__footer"><span className="local-mark" />Local services</div>
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
