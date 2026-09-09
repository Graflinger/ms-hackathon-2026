import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { registryApi, type Project, type MetadataPatch } from "../api";
import {
  PageHeader,
  SectionHeading,
  ErrorState,
  Loading,
  Notice,
  Status,
} from "../components";
import { useDirty, useProject } from "../project";

export function MetadataForm({
  record,
  onSave,
  pending,
  error,
  kind,
}: {
  record?: { name: string; description: string; archived: boolean };
  onSave: (body: MetadataPatch) => void;
  pending: boolean;
  error: unknown;
  kind: "project" | "agent";
}) {
  const [name, setName] = useState(record?.name ?? "");
  const [description, setDescription] = useState(record?.description ?? "");
  return (
    <form
      className="form-stack"
      onSubmit={(e) => {
        e.preventDefault();
        if (name.trim()) onSave({ name: name.trim(), description });
      }}
    >
      <fieldset
        className="write-boundary"
        disabled={pending || record?.archived}
      >
        <label>
          {kind === "project" ? "Project name" : "Agent name"}
          <input
            required
            maxLength={200}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label>
          Description
          <textarea
            rows={3}
            maxLength={4000}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
        <button className="button" disabled={!name.trim()}>
          {pending ? "Saving..." : record ? `Save ${kind}` : `Create ${kind}`}
        </button>
      </fieldset>
      {record && (
        <button
          type="button"
          className="button secondary align-start"
          disabled={pending}
          onClick={() => {
            if (
              !record.archived &&
              !window.confirm(
                `Archive this ${kind}? New work will be blocked. History, cancellation and exports remain available.`,
              )
            )
              return;
            onSave({ archived: !record.archived });
          }}
        >
          {record.archived ? "Unarchive" : "Archive"} {kind}
        </button>
      )}
      {!!error && <ErrorState error={error} />}
    </form>
  );
}

export function ProjectSettings({ project }: { project: Project }) {
  const { metadataError } = useProject();
  const client = useQueryClient();
  const [dirty, setDirty] = useState(false);
  useDirty(dirty);
  const save = useMutation({
    mutationFn: (body: MetadataPatch) => {
      if (metadataError)
        throw new Error("Verify project metadata before editing.");
      return registryApi.updateProject(project.id, body);
    },
    onSuccess: () => {
      setDirty(false);
      client.invalidateQueries({ queryKey: ["projects"] });
      client.invalidateQueries({ queryKey: ["project", project.id] });
    },
  });
  return (
    <details className="panel project-settings">
      <summary>Project settings / {project.name}</summary>
      <fieldset
        className="write-boundary"
        disabled={!!metadataError}
        onChangeCapture={() => setDirty(true)}
      >
        <MetadataForm
          key={`${project.name}-${project.description}-${project.archived}`}
          record={project}
          onSave={(body) => save.mutate(body)}
          pending={save.isPending}
          error={save.error}
          kind="project"
        />
      </fieldset>
    </details>
  );
}

export default function Projects() {
  const client = useQueryClient();
  const [editing, setEditing] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: ({ signal }) => registryApi.projects(signal),
  });
  const create = useMutation({
    mutationFn: (body: MetadataPatch) =>
      registryApi.createProject({
        name: body.name!,
        description: body.description,
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["projects"] });
      setCreating(false);
    },
  });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: MetadataPatch }) =>
      registryApi.updateProject(id, body),
    onSuccess: (_, { id }) => {
      client.invalidateQueries({ queryKey: ["projects"] });
      client.invalidateQueries({ queryKey: ["project", id] });
      setEditing(null);
    },
  });
  let lastUsed: string | null = null;
  try {
    lastUsed = localStorage.getItem("goldenloop.project");
  } catch {
    /* Optional preference. */
  }
  return (
    <>
      <PageHeader
        eyebrow="YOUR EVALUATION WORKSPACES"
        title="Choose the work that matters."
        description="Each project has its own agents, cases, releases and evaluation history. Project boundaries organize work; they are not authentication boundaries."
        action={
          <button className="button" onClick={() => setCreating(!creating)}>
            New project
          </button>
        }
      />
      {(creating || projects.data?.length === 0) && (
        <section className="panel">
          <SectionHeading title="Create a project" />
          <MetadataForm
            kind="project"
            onSave={(body) => create.mutate(body)}
            pending={create.isPending}
            error={create.error}
          />
        </section>
      )}
      {projects.isPending ? (
        <Loading label="Loading projects..." />
      ) : projects.isError ? (
        <ErrorState error={projects.error} retry={() => projects.refetch()} />
      ) : (
        <div className="project-grid">
          {projects.data.map((project) => (
            <section className="panel project-card" key={project.id}>
              <div className="detail-body">
                <div className="inline-meta">
                  <Status value={project.archived ? "archived" : "active"} />
                  {project.id === lastUsed && (
                    <span className="small-label">LAST USED</span>
                  )}
                </div>
                <h2>{project.name}</h2>
                <p>{project.description || "No project description."}</p>
                <code>{project.id}</code>
                <div className="form-actions">
                  <Link
                    className="button"
                    to={`/projects/${encodeURIComponent(project.id)}`}
                  >
                    Open project<span className="sr-only"> {project.name}</span>
                  </Link>
                  <button
                    className="button secondary"
                    onClick={() =>
                      setEditing(editing === project.id ? null : project.id)
                    }
                  >
                    Manage<span className="sr-only"> {project.name}</span>
                  </button>
                </div>
                {project.archived && (
                  <Notice>
                    History and exports remain available. Unarchive to resume
                    new work.
                  </Notice>
                )}
              </div>
              {editing === project.id && (
                <MetadataForm
                  kind="project"
                  record={project}
                  onSave={(body) => update.mutate({ id: project.id, body })}
                  pending={update.isPending}
                  error={update.error}
                />
              )}
            </section>
          ))}
        </div>
      )}
    </>
  );
}
