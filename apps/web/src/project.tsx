import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  Link,
  useBlocker,
  UNSAFE_DataRouterContext,
  useLocation,
  useNavigate,
  type LinkProps,
} from "react-router-dom";
import { createProjectApi, type Project } from "./api";

export const DEMO_PROJECT = "synthetic-demo";
export const DEMO_AGENT = "synthetic-customer-lookup";
export const DEMO_REVISIONS = {
  buggy: "synthetic-buggy",
  fixed: "synthetic-fixed",
};
type ProjectValue = {
  project: Project;
  api: ReturnType<typeof createProjectApi>;
  dirty: Set<symbol>;
  writeBlocked: boolean;
  metadataError?: Error | null;
  retryMetadata?: () => void;
};
export const ProjectContext = createContext<ProjectValue | null>(null);

export function ProjectProvider({
  project,
  children,
  api: suppliedApi,
  metadataError,
  retryMetadata,
}: {
  project: Project;
  children: ReactNode;
  api?: ReturnType<typeof createProjectApi>;
  metadataError?: Error | null;
  retryMetadata?: () => void;
}) {
  const writeBlocked = project.archived || !!metadataError;
  const writable = useRef(!writeBlocked);
  useLayoutEffect(() => {
    writable.current = !writeBlocked;
  }, [writeBlocked]);
  const [api] = useState(
    () => suppliedApi ?? createProjectApi(project.id, () => writable.current),
  );
  const [dirty] = useState(() => new Set<symbol>());
  const dataRouter = useContext(UNSAFE_DataRouterContext);
  useEffect(() => {
    try {
      localStorage.setItem("goldenloop.project", project.id);
    } catch {
      /* Storage may be unavailable. */
    }
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (dirty.size) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [project.id, dirty]);
  return (
    <ProjectContext.Provider
      value={{
        project,
        api,
        dirty,
        writeBlocked,
        metadataError,
        retryMetadata,
      }}
    >
      {dataRouter && <NavigationGuard />}
      {children}
    </ProjectContext.Provider>
  );
}
function NavigationGuard() {
  const { dirty } = useProject();
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty.size > 0 &&
      navigationChangesResource(currentLocation, nextLocation),
  );
  useEffect(() => {
    if (blocker.state === "blocked") {
      if (confirmDiscard(dirty)) blocker.proceed();
      else blocker.reset();
    }
  }, [blocker, dirty]);
  return null;
}
function navigationChangesResource(
  current: { pathname: string; search: string },
  next: { pathname: string; search: string },
) {
  const search = (value: string) => {
    const params = new URLSearchParams(value);
    params.sort();
    return params.toString();
  };
  // Hash-only navigation (including the skip link) does not replace project resources.
  return (
    current.pathname !== next.pathname ||
    search(current.search) !== search(next.search)
  );
}
export function useProject() {
  const value = useContext(ProjectContext);
  if (!value) throw new Error("Project context is required");
  return value;
}
export function useProjectApi() {
  return useProject().api;
}
export function useDirty(isDirty: boolean) {
  const { dirty } = useProject();
  const [token] = useState(() => Symbol());
  useEffect(() => {
    if (isDirty) dirty.add(token);
    else dirty.delete(token);
    return () => {
      dirty.delete(token);
    };
  }, [dirty, token, isDirty]);
  // Successful saves clear only their own draft before navigating synchronously.
  return () => {
    dirty.delete(token);
  };
}
export function useInstanceGuard() {
  const instance = useRef<symbol | null>(null);
  useLayoutEffect(() => {
    instance.current = Symbol();
    return () => {
      instance.current = null;
    };
  }, []);
  return () => {
    const submittedBy = instance.current;
    return () => submittedBy !== null && instance.current === submittedBy;
  };
}
export function confirmDiscard(dirty: Set<symbol>) {
  if (!dirty.size) return true;
  if (
    !window.confirm(
      "Discard unsaved project changes? Running work will continue in its original project.",
    )
  )
    return false;
  return true;
}
// A completed old mutation may invalidate old data, but must not navigate the new project.
export function useProjectSearchParams() {
  const location = useLocation();
  const navigate = useNavigate();
  const { dirty } = useProject();
  const dataRouter = useContext(UNSAFE_DataRouterContext);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  return [
    new URLSearchParams(location.search),
    (values: Record<string, string>) => {
      if (!alive.current) return;
      const next = {
        pathname: location.pathname,
        search: new URLSearchParams(values).toString(),
      };
      if (
        !dataRouter &&
        navigationChangesResource(location, next) &&
        !confirmDiscard(dirty)
      )
        return;
      navigate(next);
    },
  ] as const;
}
export function ProjectLink({ to, ...props }: LinkProps) {
  const { project } = useProject();
  const scoped =
    typeof to === "string" && to.startsWith("/") && !to.startsWith("/projects")
      ? `/projects/${encodeURIComponent(project.id)}${to === "/" ? "" : to}`
      : to;
  return <Link to={scoped} {...props} />;
}
