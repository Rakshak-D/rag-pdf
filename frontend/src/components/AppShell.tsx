import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";

interface AppShellProps {
  children: ReactNode;
}

const sections = [
  { label: "Chat", to: "/", icon: <><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-5 4v-4.8A2.5 2.5 0 0 1 4 13.5z"/><path d="M8 8h8M8 11.5h5"/></> },
  { label: "Library", to: "/documents", icon: <><path d="M6 3.5h8l4 4v13H6z"/><path d="M14 3.5v4h4M9 12h6M9 15.5h6"/></> },
  { label: "Settings", to: "/settings", icon: <><circle cx="12" cy="12" r="3"/><path d="m19.4 15 .1.1 1.1.9-1.1 1.9-1.3-.5a7 7 0 0 1-1.7 1l-.2 1.4h-2.2l-.3-1.4a7 7 0 0 1-1.8-1l-1.2.5-1.1-1.9 1.1-.9a6 6 0 0 1 0-2l-1.1-.9 1.1-1.9 1.3.5a7 7 0 0 1 1.7-1l.3-1.4h2.2l.2 1.4a7 7 0 0 1 1.8 1l1.2-.5 1.1 1.9-1.1.9a6 6 0 0 1 0 2Z" transform="translate(-1 -1)"/></> },
];

function sectionFor(pathname: string): string {
  if (pathname === "/" || pathname.startsWith("/ask")) return "Chat";
  if (pathname.startsWith("/documents")) return "Library";
  if (pathname.startsWith("/settings")) return "Settings";
  if (pathname === "/research" || pathname.startsWith("/artifacts") || pathname.startsWith("/jobs") ||
    pathname.startsWith("/experiments") || pathname.startsWith("/index-builds") || pathname === "/overview") return "Advanced Research";
  return "Not found";
}

export function AppShell({ children }: AppShellProps) {
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const sidebar = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!mobileOpen) return;
    const focusable = () => sidebar.current?.querySelectorAll<HTMLElement>("a[href], button:not([disabled])") ?? [];
    focusable()[0]?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMobileOpen(false);
        menuButton.current?.focus();
        return;
      }
      if (event.key === "Tab") {
        const items = focusable();
        if (!items.length) return;
        const first = items[0];
        const last = items[items.length - 1];
        if (!first || !last) return;
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [mobileOpen]);

  useEffect(() => { setMobileOpen(false); }, [location.pathname]);

  const title = sectionFor(location.pathname);
  const closeMobileToMain = () => {
    setMobileOpen(false);
    requestAnimationFrame(() => document.getElementById("main-content")?.focus());
  };
  const closeMobileToButton = () => {
    setMobileOpen(false);
    menuButton.current?.focus();
  };

  return <div className={`app-frame${collapsed ? " app-frame--collapsed" : ""}${mobileOpen ? " app-frame--mobile-open" : ""}`}>
    <a className="skip-link" href="#main-content">Skip to content</a>
    {mobileOpen ? <button className="mobile-scrim" type="button" aria-label="Close navigation" onClick={closeMobileToButton} /> : null}
    <aside ref={sidebar} id="main-sidebar" className={`sidebar${mobileOpen ? " sidebar--mobile-open" : ""}`} aria-label="Primary sidebar" role={mobileOpen ? "dialog" : undefined} aria-modal={mobileOpen || undefined}>
      <div className="sidebar__top">
        <Link className="brand" to="/" aria-label="RAG-PDF home">
          <svg className="brand__mark" viewBox="0 0 36 36" aria-hidden="true">
            <path d="M8 4.5h13l7 7V31H8z" fill="none" stroke="currentColor" strokeWidth="1.5" />
            <path d="M21 4.5v7h7M12 17h12M12 21h8M12 25h10" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.4" />
            <path d="m24.7 22.8 3.7 3.7" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.5" />
          </svg>
          <span className="brand__copy"><strong>RAG-PDF</strong><small>Local document chat</small></span>
        </Link>
        <button className="sidebar-collapse" type="button" onClick={() => setCollapsed((value) => !value)} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} aria-expanded={!collapsed} title={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
          <span aria-hidden="true">{collapsed ? "›" : "‹"}</span>
        </button>
      </div>
      <nav aria-label="Primary navigation" className="nav-list">
        {sections.map((item) => <Link
          key={item.to}
          to={item.to}
          title={collapsed ? item.label : undefined}
          aria-label={item.label}
          aria-current={sectionFor(location.pathname) === item.label ? "page" : undefined}
          className={`nav-item${sectionFor(location.pathname) === item.label ? " nav-item--active" : ""}`}
          onClick={() => mobileOpen && closeMobileToMain()}
        ><svg className="nav-icon" viewBox="0 0 24 24" aria-hidden="true">{item.icon}</svg><span className="nav-label">{item.label}</span></Link>)}
      </nav>
      <div className="sidebar__footer"><span className="local-mark" aria-hidden="true" /><span className="sidebar__footer-label">Loopback access</span></div>
    </aside>
    <div className="app-content">
      <header className="topbar">
        <button ref={menuButton} type="button" className="mobile-menu-button" aria-label="Open navigation" aria-expanded={mobileOpen} aria-controls="main-sidebar" onClick={() => setMobileOpen((value) => !value)}><span aria-hidden="true">☰</span></button>
        <div className="topbar__crumb"><span>RAG-PDF</span><span className="crumb-separator">/</span><strong>{title}</strong></div>
        <span className="topbar__local"><span className="local-mark" aria-hidden="true" /> Local workspace</span>
      </header>
      <main id="main-content" className="main-content" tabIndex={-1}>{children}</main>
    </div>
  </div>;
}
