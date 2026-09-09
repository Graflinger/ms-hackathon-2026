import { useState } from "react";
import {
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";
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
import { registryApi as api } from "./api";
import { EmptyState, ErrorState, Loading, Notice, Status } from "./components";
import { ProjectProvider, useProject, DEMO_PROJECT } from "./project";
import Projects, { ProjectSettings } from "./pages/Projects";
import Agents from "./pages/Agents";
import Overview from "./pages/Overview";
import ImportPage from "./pages/Import";
import Playground from "./pages/Playground";
import Candidates from "./pages/Candidates";
import Releases from "./pages/Releases";
import Runs from "./pages/Runs";

const navigation = [
  { to: "/", label: "Overview", icon: LayoutDashboard, number: "01" },
  { to: "/agents", label: "Agents", icon: Activity, number: "02" },
  { to: "/import", label: "Import", icon: Upload, number: "03" },
  { to: "/playground", label: "Playground", icon: MessageSquare, number: "04" },
  { to: "/candidates", label: "Candidates", icon: CheckCheck, number: "05" },
  { to: "/releases", label: "Releases", icon: BookOpen, number: "06" },
  { to: "/runs", label: "Evaluation runs", icon: FlaskConical, number: "07" },
];

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Workbench />} />
      <Route path="/projects/:projectId/*" element={<ProjectRoute />} />
      {navigation
        .filter((item) => item.to !== "/")
        .map((item) => (
          <Route
            key={item.to}
            path={`${item.to}/*`}
            element={<LegacyRedirect />}
          />
        ))}
      <Route path="*" element={<Workbench />} />
    </Routes>
  );
}

function LegacyRedirect() {
  const location = useLocation();
  return (
    <Navigate
      replace
      to={`/projects/${DEMO_PROJECT}${location.pathname}${location.search}${location.hash}`}
    />
  );
}

function ProjectRoute() {
  const { projectId = "" } = useParams();
  const project = useQuery({
    queryKey: ["project", projectId, "metadata"],
    queryFn: ({ signal }) => api.project(projectId, signal),
  });
  if (project.isPending) return <Loading label="Opening project..." />;
  if (!project.data)
    return (
      <div className="detail-body">
        <ErrorState error={project.error} retry={() => project.refetch()} />
        <NavLink to="/">Choose another project</NavLink>
      </div>
    );
  return (
    <ProjectProvider
      key={projectId}
      project={project.data}
      metadataError={project.error}
      retryMetadata={() => {
        void project.refetch();
      }}
    >
      <Workbench projectScoped />
    </ProjectProvider>
  );
}

function ProjectPicker() {
  const { project } = useProject();
  const navigate = useNavigate();
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: ({ signal }) => api.projects(signal),
  });
  return (
    <div className="project-picker">
      <label>
        Current project
        <select
          value={project.id}
          onChange={(e) => {
            navigate(`/projects/${encodeURIComponent(e.target.value)}`);
          }}
        >
          {(projects.data ?? [project]).map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
              {p.archived ? " / archived" : ""}
            </option>
          ))}
        </select>
      </label>
      {projects.isError && (
        <ErrorState error={projects.error} retry={() => projects.refetch()} />
      )}
      <NavLink to="/">All projects / manage</NavLink>
    </div>
  );
}

function ScopedContent() {
  const { project, metadataError, retryMetadata } = useProject();
  return (
    <>
      {metadataError && (
        <section aria-label="Project metadata unavailable">
          <Notice tone="warning">
            Project metadata could not be refreshed. Your drafts are retained.
            New writes are disabled until archive state can be verified;
            history, cancellation and exports remain available.
          </Notice>
          <ErrorState error={metadataError} retry={retryMetadata} />
        </section>
      )}
      {project.archived && (
        <Notice tone="warning">
          This project is archived. New work is disabled; history, cancellation
          and explicit exports remain available.
        </Notice>
      )}
      <ProjectSettings project={project} />
      <Routes>
        <Route index element={<Overview />} />
        <Route path="overview" element={<Overview />} />
        <Route path="agents" element={<Agents />} />
        <Route path="import" element={<ImportPage />} />
        <Route path="playground" element={<Playground />} />
        <Route path="candidates" element={<Candidates />} />
        <Route path="releases" element={<Releases />} />
        <Route path="runs" element={<Runs />} />
        <Route
          path="*"
          element={
            <EmptyState title="Page not found">
              Choose a workbench screen from the project navigation.
            </EmptyState>
          }
        />
      </Routes>
    </>
  );
}

function Workbench({ projectScoped = false }: { projectScoped?: boolean }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();
  const { projectId } = useParams();
  const prefix = projectScoped
    ? `/projects/${encodeURIComponent(projectId!)}`
    : "";
  const health = useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) => api.health(signal),
    refetchInterval: 30_000,
  });
  const current =
    navigation.find(
      (item) =>
        `${prefix}${item.to === "/" ? "" : item.to}` === location.pathname,
    )?.label || (projectScoped ? "Workbench" : "Projects");
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
        {projectScoped && <ProjectPicker />}
        <p className="nav-caption">
          {projectScoped ? "WORKBENCH" : "PROJECTS"}
        </p>
        <nav>
          {projectScoped ? (
            navigation.map(({ to, label, icon: Icon, number }) => (
              <NavLink
                key={to}
                to={`${prefix}${to === "/" ? "" : to}`}
                end={to === "/"}
                onClick={() => setMenuOpen(false)}
              >
                <Icon size={18} />
                <span>{label}</span>
                <small>{number}</small>
              </NavLink>
            ))
          ) : (
            <NavLink to="/" end>
              <LayoutDashboard size={18} />
              <span>Project chooser</span>
            </NavLink>
          )}
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
          {projectScoped ? <ScopedContent /> : <Projects />}
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
