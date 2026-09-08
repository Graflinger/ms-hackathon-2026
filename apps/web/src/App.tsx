import { useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArrowUpRight,
  BookOpen,
  CheckCheck,
  FlaskConical,
  LayoutDashboard,
  Menu,
  MessageSquare,
  Upload,
  X,
} from "lucide-react";
import { api } from "./api";
import { EmptyState, Status } from "./components";
import Overview from "./pages/Overview";
import ImportPage from "./pages/Import";
import Playground from "./pages/Playground";
import Candidates from "./pages/Candidates";
import Releases from "./pages/Releases";
import Runs from "./pages/Runs";

const navigation = [
  { to: "/", label: "Overview", icon: LayoutDashboard, number: "01" },
  { to: "/import", label: "Import", icon: Upload, number: "02" },
  { to: "/playground", label: "Playground", icon: MessageSquare, number: "03" },
  { to: "/candidates", label: "Candidates", icon: CheckCheck, number: "04" },
  { to: "/releases", label: "Releases", icon: BookOpen, number: "05" },
  { to: "/runs", label: "Evaluation runs", icon: FlaskConical, number: "06" },
];

export default function App() {
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();
  const health = useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) => api.health(signal),
    refetchInterval: 30_000,
  });
  const current =
    navigation.find((item) => item.to === location.pathname)?.label ||
    "Workbench";
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      {menuOpen && (
        <button
          className="sidebar-scrim"
          aria-label="Close navigation"
          onClick={() => setMenuOpen(false)}
        />
      )}
      <aside
        className={`sidebar ${menuOpen ? "open" : ""}`}
        aria-label="Main navigation"
      >
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            g<span>l</span>
          </span>
          <div>
            GoldenLoop<small>THE EVALUATION WORKBENCH</small>
          </div>
          <button
            className="icon-button mobile-close"
            aria-label="Close navigation"
            onClick={() => setMenuOpen(false)}
          >
            <X size={20} />
          </button>
        </div>
        <div className="workspace-label">
          <span className="workspace-dot" />
          Hack for Evals <span className="workspace-version">01</span>
        </div>
        <p className="nav-caption">WORKBENCH</p>
        <nav>
          {navigation.map(({ to, label, icon: Icon, number }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              onClick={() => setMenuOpen(false)}
            >
              <Icon size={18} />
              <span>{label}</span>
              <small>{number}</small>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-note">
          <span className="small-label">THE GOLDEN RULE</span>
          <p>
            Observe first.
            <br />
            Review deliberately.
            <br />
            <em>Improve with evidence.</em>
          </p>
          <span className="sidebar-rule" />
        </div>
        <div className="sidebar-bottom">
          <Activity size={15} />
          <div>
            <span>
              {health.isError
                ? "API unavailable"
                : health.isPending
                  ? "Connecting to API"
                  : `API ${health.data.status}`}
            </span>
            <small>
              {health.data
                ? `${health.data.mode} / SDK ${health.data.sdk_version}`
                : "Local workbench"}
            </small>
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <div className="topbar">
          <div className="breadcrumbs">
            <button
              className="icon-button mobile-menu"
              aria-label="Open navigation"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen(true)}
            >
              <Menu size={21} />
            </button>
            <span>Workspace</span>
            <span className="slash">/</span>
            <strong>{current}</strong>
          </div>
          <div className="topbar-meta">
            <span className="synthetic-badge">SYNTHETIC DATA</span>
            {health.data && (
              <span className="mode-label">{health.data.mode} mode</span>
            )}
            <ArrowUpRight size={16} aria-hidden="true" />
          </div>
        </div>
        <main id="main" tabIndex={-1}>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/import" element={<ImportPage />} />
            <Route path="/playground" element={<Playground />} />
            <Route path="/candidates" element={<Candidates />} />
            <Route path="/releases" element={<Releases />} />
            <Route path="/runs" element={<Runs />} />
            <Route
              path="*"
              element={
                <EmptyState
                  title="Page not found"
                  action={
                    <NavLink className="button" to="/">
                      Return to overview
                    </NavLink>
                  }
                >
                  This route is not part of the workbench.
                </EmptyState>
              }
            />
          </Routes>
        </main>
        <footer className="footer">
          <span>
            GoldenLoop <span className="footer-dot">/</span> Evidence, not
            assumptions.
          </span>
          <span>
            {health.data ? (
              <Status value={health.data.status} />
            ) : (
              "Backend connection required"
            )}
          </span>
        </footer>
      </div>
    </div>
  );
}
